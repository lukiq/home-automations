"""Exercise the actual garden YAML on ESPHome's native host, with no device access."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tests'))
from test_stairs import ConfigLoader, Lambda
import yaml


def build_config(source, selected):
    config = yaml.load(source.read_text(), Loader=ConfigLoader)
    for key in ('esp32', 'wifi', 'api', 'ota', 'captive_portal', 'web_server'):
        config.pop(key, None)
    config['host'] = {}
    boot_actions = config['esphome'].get('on_boot', {}).get('then', [])
    config['esphome'] = {'name': 'garden-regression-host',
                         'on_boot': {'priority': -100, 'then': list(boot_actions)}}
    config['logger'] = {'level': 'DEBUG'}
    for sensor in config['binary_sensor']:
        sensor['platform'] = 'template'
        sensor.pop('pin')
    for i, valve in enumerate(config['sprinkler'][0]['valves'], 1):
        if isinstance(valve['valve_switch'], str):
            valve['valve_switch'] = {'name': valve['valve_switch'], 'id': f'section_{i}_control'}
        valve['enable_switch'] = {'name': f'Enable Section {i}', 'id': f'review_enable_{i}'}
    # Keep GPIOSwitch itself: host GPIO digital_write only logs and touches no hardware.
    common = 'id(garden_sprinkler_ctrlr).set_multiplier(1.2f);\n'
    common += 'for(size_t i=0;i<5;i++) id(garden_sprinkler_ctrlr).set_valve_run_duration(i,10);\n'
    common += '\n'.join(f'id(section_{i}_parallel).publish_state({value}); '
                        f'id(review_enable_{i}).turn_on();'
                        for i, value in enumerate((5, 5, 4, 0, 0), 1))
    def code(text):
        return {'lambda': Lambda(text)}
    def delay(ms):
        return {'delay': f'{ms}ms'}
    def mapping(values):
        return code('\n'.join(f'id(section_{i}_parallel).publish_state({value});'
                              for i, value in enumerate(values, 1)))
    def start(section):
        return {'sprinkler.start_single_valve': {'id': 'garden_sprinkler_ctrlr',
                                                'valve_number': section - 1}}
    def check(label, mask, active=None):
        expression = ' && '.join(('' if mask & (1 << (i-1)) else '!') +
                                 f'id(section_{i}).state' for i in range(1, 6))
        expression += ' && ' + ' && '.join(('' if mask & (1 << (i-1)) else '!') +
                                           f'id(section_{i}_control).state' for i in range(1, 6))
        if active is not None:
            expression += (' && !id(garden_sprinkler_ctrlr).active_valve().has_value()'
                           if active == 0 else
                           f' && id(garden_sprinkler_ctrlr).active_valve().value_or(99)=={active-1}')
        expression += ' && ' + ('' if mask else '!') + 'id(main_section).state'
        return code(f'ESP_LOGI("garden_test", "{label} active=%d main_valve=%d valves=%d%d%d%d%d", '
                    'id(garden_sprinkler_ctrlr).active_valve().has_value()?'
                    'int(id(garden_sprinkler_ctrlr).active_valve().value())+1:0, '
                    'id(main_section).state, '+
                    ','.join(f'id(section_{i}).state' for i in range(1, 6))+
                    f'); if(!({expression})) {{ ESP_LOGE("garden_test","ASSERTION FAILED"); exit(2); }}')
    stop = {'sprinkler.shutdown': 'garden_sprinkler_ctrlr'}
    cycle = {'sprinkler.start_full_cycle': 'garden_sprinkler_ctrlr'}
    cases = {
        'current_cycle': [cycle, delay(27000), check('section4_retained', 8, 4),
                          stop, delay(100), check('shutdown', 0, 0)],
        'direct_overlap': [mapping((2, 0, 0, 0, 0)),
                           code('id(review_enable_3).turn_off(); id(review_enable_4).turn_off(); '
                                'id(review_enable_5).turn_off();'),
                           cycle, delay(13000), check('section2_retained', 2, 2)],
        'shared_target': [code('id(review_enable_3).turn_off(); id(review_enable_4).turn_off(); '
                               'id(review_enable_5).turn_off();'), cycle, delay(13000),
                          check('shared5_retained', 18, 2), stop, delay(100), check('shutdown', 0, 0)],
        'mapping_change': [mapping((2, 0, 0, 0, 0)), start(1), delay(300),
                           check('old_companion', 3, 1), mapping((3, 0, 0, 0, 0)), delay(100),
                           check('new_companion', 5, 1), mapping((0, 0, 0, 0, 0)), delay(100),
                           check('companion_removed', 1, 1), stop, delay(100), check('shutdown', 0, 0)],
        'invalid_mapping': [start(1), delay(300),
            code('id(section_1_parallel).publish_state(NAN);'), delay(100), check('unknown_mapping', 1, 1),
            code('id(section_1_parallel).publish_state(INFINITY);'), delay(100), check('infinite_mapping', 1, 1),
            code('id(section_1_parallel).publish_state(2.5f);'), delay(100), check('fractional_mapping', 1, 1),
            stop, delay(100), check('shutdown', 0, 0)],
        'chain': [mapping((2, 3, 0, 0, 0)), start(1), delay(300), check('chain', 7, 1),
                  stop, delay(100), check('chain_shutdown', 0, 0)],
        'cycle': [mapping((2, 1, 0, 0, 0)), start(1), delay(300), check('cycle', 3, 1),
                  stop, delay(100), check('cycle_shutdown', 0, 0)],
    }
    for button, sensor, initial, next_section in [('up', 'gpio19_sensor', 1, 2),
                                                 ('down', 'gpio22_sensor', 2, 1)]:
        for duration in (300, 850, 1000, 1100, 1500, 2000, 3000):
            actions = [code('for(size_t i=0;i<5;i++) '
                            'id(garden_sprinkler_ctrlr).set_valve_run_duration(i,60);'),
                       start(initial), delay(300),
                       {'binary_sensor.template.publish': {'id': sensor, 'state': True}},
                       delay(duration)]
            if duration >= 1100:
                actions.append(check('stop_while_held', 0, 0))
            actions.extend([{'binary_sensor.template.publish': {'id': sensor, 'state': False}},
                            delay(3200), check('after_release', 0 if duration >= 1000 else
                            (1 << (next_section-1)) | 16, 0 if duration >= 1000 else next_section)])
            cases[f'{button}_{duration}'] = actions
    if selected:
        cases = {name: cases[name] for name in selected}
    config.setdefault('script', []).extend(
        {'id': 'review_'+name, 'then': [code(common), delay(200)] + actions + [code('exit(0);')]}
        for name, actions in cases.items())
    boot = config['esphome']['on_boot']['then']
    boot.extend({'binary_sensor.template.publish': {'id': s['id'], 'state': False}}
                for s in config['binary_sensor'])
    boot.extend([delay(300), code('const char *name=getenv("GARDEN_TEST_CASE"); '
                                 'if(!name) exit(3);\n' + '\n'.join(
        f'if(std::string(name)=="{name}") {{id(review_{name}).execute(); return;}}' for name in cases)
        + '\nexit(3);')])
    return config, cases


async def execute(binary, output, names):
    async def run(name):
        folder = output / name
        folder.mkdir(exist_ok=True)
        env = dict(os.environ, GARDEN_TEST_CASE=name, ESPHOME_PREFDIR=str(folder / 'preferences'))
        process = await asyncio.create_subprocess_exec(str(binary), cwd=folder, env=env,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            data, _ = await asyncio.wait_for(process.communicate(), timeout=40)
        except asyncio.TimeoutError:
            process.kill()
            data, _ = await process.communicate()
        (folder / 'runtime.log').write_bytes(data)
        log = re.sub(r'\x1b\[[0-9;]*m', '', data.decode(errors='replace'))
        return {'case': name, 'exit': process.returncode,
                'observations': [line for line in log.splitlines() if '[garden_test:' in line]}
    return await asyncio.gather(*(run(name) for name in names))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--esphome', default='esphome')
    parser.add_argument('--source', type=Path, default=ROOT / 'esp-home/garden-house-controller.yaml')
    parser.add_argument('--output', type=Path, default=ROOT / '.context/garden-tests')
    parser.add_argument('--case', action='append')
    args = parser.parse_args()
    version = subprocess.check_output([args.esphome, 'version'], text=True)
    if '2026.8.2' not in version:
        parser.error('Use ESPHome 2026.8.2 to match the deployed garden controller')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config, cases = build_config(args.source, args.case)
    class Dumper(yaml.SafeDumper):
        pass
    Dumper.add_representer(Lambda, lambda d, s: d.represent_scalar('!lambda', str(s), style='|'))
    path = output / 'host.yaml'
    path.write_text(yaml.dump(config, Dumper=Dumper, sort_keys=False, allow_unicode=True))
    with (output / 'compile.log').open('w') as log:
        subprocess.run([args.esphome, 'compile', str(path)], check=True, stdout=log, stderr=subprocess.STDOUT)
    binary = output / '.esphome/build/garden-regression-host/.pioenvs/garden-regression-host/program'
    results = asyncio.run(execute(binary, output, cases))
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    for result in results:
        print(json.dumps(result))
    return int(any(result['exit'] != 0 for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
