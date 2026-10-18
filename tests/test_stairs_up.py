"""Replay actual ESPHome YAML actions with virtual time, not a second controller.

Models the used script/condition/light actions; unknown operations fail closed.
GPIO events are already debounced. This is not an ESPHome or electrical emulator.
Requires PyYAML (also an ESPHome dependency).
"""
import heapq
import itertools
import os
from pathlib import Path
import re
import unittest

import yaml


class Lambda(str):
    pass


class ConfigLoader(yaml.SafeLoader):
    pass


ConfigLoader.add_constructor('!lambda', lambda loader, node: Lambda(loader.construct_scalar(node)))
ConfigLoader.add_constructor('!secret', lambda loader, node: loader.construct_scalar(node))
SOURCE = Path(os.environ.get('STAIRS_CONFIG', Path(__file__).parents[1] / 'esp-home/esp32-stairs-up.yaml'))
CONFIG = yaml.load(SOURCE.read_text(), Loader=ConfigLoader)


class Replay:
    def __init__(self):
        self.now = 0
        self.queue = []
        self.sequence = itertools.count()
        self.running = {}
        self.scripts = {s['id']: s for s in CONFIG['script']}
        self.sensors = {int(str(s['pin']['number']).removeprefix('GPIO')): s for s in CONFIG['binary_sensor']}
        # Fallback IDs permit running the same scenarios against the pre-fix YAML.
        self.sensor_values = {}
        for pin, sensor in self.sensors.items():
            self.sensor_values[sensor.get('id', str(pin))] = False
        self.switches = {s['id']: s for s in CONFIG['switch'] if 'id' in s}
        self.switch_values = dict.fromkeys(self.switches, False)
        self.lights = {s['id']: s for s in CONFIG['light']}
        self.light_values = dict.fromkeys(self.lights, False)
        self.pixels = [False] * self.lights['stairs_up']['num_leds']
        self.writes = []
        self.numbers = {}
        for number in CONFIG['number']:
            initial = number['initial_value']
            if isinstance(initial, str) and initial.startswith('${'):
                initial = CONFIG['substitutions'][initial[2:-1]]
            self.numbers[number['id']] = float(initial)

    def value(self, expr, args):
        if not isinstance(expr, Lambda):
            if isinstance(expr, str) and expr.endswith('ms'):
                return float(expr[:-2])
            if isinstance(expr, str) and expr.endswith('s'):
                return float(expr[:-1]) * 1000
            return expr
        text = expr.strip()
        if text == 'return !id(stairs_up).remote_values.is_on();':
            return not self.light_values['stairs_up']
        if text == 'return down_to_up;':
            return args['down_to_up']
        match = re.fullmatch(r'return id\((\w+)\)\.state(?: \* (1000))?;', text)
        if not match:
            raise AssertionError(f'Unsupported lambda: {text}')
        return self.numbers[match[1]] * (1000 if match[2] else 1)

    def condition(self, condition, args):
        if isinstance(condition, list):
            return all(self.condition(c, args) for c in condition)
        key, value = next(iter(condition.items()))
        if key == 'and':
            return all(self.condition(c, args) for c in value)
        if key == 'not':
            return not self.condition(value, args)
        if key == 'lambda':
            return bool(self.value(Lambda(value), args))
        if key == 'script.is_running':
            return value in self.running
        for prefix, states in [('switch', self.switch_values), ('binary_sensor', self.sensor_values), ('light', self.light_values)]:
            if key == prefix + '.is_on':
                return states[value]
            if key == prefix + '.is_off':
                return not states[value]
        raise AssertionError(f'Unsupported condition: {condition}')

    def execute(self, name, args=None):
        script = self.scripts[name]
        if name in self.running:
            if script.get('mode', 'single') == 'single':
                return
            assert script['mode'] == 'restart'
        token = object()
        self.running[name] = token
        self.resume(name, token, self.actions(script['then'], args or {}))

    def resume(self, name, token, generator):
        if self.running.get(name) is not token:
            return
        try:
            delay = next(generator)
        except StopIteration:
            if self.running.get(name) is token:
                del self.running[name]
        else:
            heapq.heappush(self.queue, (self.now + delay, next(self.sequence), name, token, generator))

    def actions(self, actions, args=None):
        args = args or {}
        if isinstance(actions, dict):
            actions = actions.get('then', [actions])
        for action in actions:
            key, value = next(iter(action.items()))
            if key == 'if':
                branch = 'then' if self.condition(value['condition'], args) else 'else'
                yield from self.actions(value.get(branch, []), args)
            elif key == 'wait_until':
                while not self.condition(value, args):
                    yield 1
            elif key == 'delay':
                yield self.value(value, args)
            elif key == 'script.execute':
                if isinstance(value, str):
                    self.execute(value)
                else:
                    self.execute(value['id'], {k: v for k, v in value.items() if k != 'id'})
            elif key == 'script.stop':
                self.running.pop(value, None)
            elif key in ('light.turn_on', 'light.turn_off'):
                state = key == 'light.turn_on'
                name = value if isinstance(value, str) else value['id']
                self.light_values[name] = state
                segments = self.lights[name].get('segments', [{'from': 0, 'to': len(self.pixels) - 1}])
                for segment in segments:
                    for index in range(segment['from'], segment['to'] + 1):
                        self.pixels[index] = state
                self.writes.append((self.now, name, state))
            elif key == 'switch.toggle':
                self.set_switch(value, not self.switch_values[value])
            elif key == 'logger.log':
                pass
            else:
                raise AssertionError(f'Unsupported action: {key}')

    def immediate(self, actions):
        # Sensor/switch handlers in this config contain no asynchronous waits.
        for _ in self.actions(actions):
            raise AssertionError('Unexpected delay in an event handler')

    def sensor(self, pin, state):
        sensor = self.sensors[pin]
        name = sensor.get('id', str(pin))
        if self.sensor_values[name] == state:
            return
        self.sensor_values[name] = state
        self.immediate(sensor.get('on_press' if state else 'on_release', []))

    def set_switch(self, name, state):
        switch = self.switches[name]
        self.immediate(switch.get('turn_on_action' if state else 'turn_off_action', []))
        if self.switch_values[name] != state:
            self.switch_values[name] = state
            self.immediate(switch.get('on_turn_on' if state else 'on_turn_off', []))

    def advance(self, milliseconds):
        end = self.now + milliseconds
        while self.queue and self.queue[0][0] <= end:
            self.now, _, name, token, generator = heapq.heappop(self.queue)
            self.resume(name, token, generator)
        self.now = end


class StairsScenarios(unittest.TestCase):
    def setUp(self):
        self.r = Replay()

    def all_lit(self):
        self.assertTrue(all(self.r.pixels))

    def all_dark(self):
        self.assertFalse(any(self.r.pixels))

    def test_both_directions_visit_every_step_in_order(self):
        for pin, expected in [(13, list(range(1, 17))), (34, list(range(16, 0, -1)))]:
            with self.subTest(pin=pin):
                self.r = Replay()
                self.r.sensor(pin, True)
                self.r.sensor(pin, False)
                self.r.advance(17000)
                steps = [int(name.removeprefix('stairs_up_step')) for _, name, on in self.r.writes if on and name.startswith('stairs_up_step')]
                self.assertEqual(steps, expected)
                self.all_lit()
                self.r.advance(20000)
                self.all_dark()

    def test_late_motion_restarts_hold_without_reanimating(self):
        self.r.sensor(13, True)
        self.r.sensor(13, False)
        self.r.advance(24000)
        writes = len(self.r.writes)
        self.r.sensor(13, True)
        self.r.sensor(13, False)
        self.r.advance(3000)
        self.all_lit()  # Past the old shutdown deadline.
        self.assertEqual(len(self.r.writes), writes)
        self.r.advance(18000)
        self.all_dark()

    def test_opposite_sensor_release_cannot_interrupt_animation(self):
        self.r.sensor(34, True)
        self.r.advance(300)
        self.r.sensor(13, True)
        self.r.sensor(13, False)
        self.r.advance(1000)
        self.assertTrue(any(self.r.pixels))
        self.assertFalse(any(not state and name.startswith('stairs_up_step') for _, name, state in self.r.writes))

    def test_held_sensor_keeps_light_on_then_full_hold_after_release(self):
        self.r.sensor(13, True)
        self.r.advance(90000)
        self.all_lit()
        self.r.sensor(13, False)
        self.r.advance(19000)
        self.all_lit()
        self.r.advance(1100)
        self.all_dark()

    def test_manual_on_cancels_animation_and_stays_on(self):
        self.r.sensor(13, True)
        self.r.advance(500)
        self.r.set_switch('SwitchStairsUpSensorsDisabled', True)
        self.r.sensor(13, False)
        self.r.sensor(34, True)
        self.r.sensor(34, False)
        self.r.advance(90000)
        self.all_lit()
        self.assertFalse(self.r.running)

    def test_manual_off_resumes_active_sensor(self):
        self.r.set_switch('SwitchStairsUpSensorsDisabled', True)
        self.r.sensor(34, True)
        self.r.set_switch('SwitchStairsUpSensorsDisabled', False)
        self.r.advance(90000)
        self.all_lit()
        self.r.sensor(34, False)
        self.r.advance(21000)
        self.all_dark()

    def test_manual_off_without_motion_leaves_dark(self):
        self.r.set_switch('SwitchStairsUpSensorsDisabled', True)
        self.r.set_switch('SwitchStairsUpSensorsDisabled', False)
        self.r.advance(90000)
        self.all_dark()

    def test_explicit_direction_switch_off_cancels_animation(self):
        self.r.set_switch('RunDownToUpOnStairsUp', True)
        self.r.advance(500)
        self.r.set_switch('RunDownToUpOnStairsUp', False)
        self.r.advance(90000)
        self.all_dark()

    def test_ha_cannot_start_two_opposing_animations(self):
        self.r.set_switch('RunDownToUpOnStairsUp', True)
        self.r.set_switch('RunUpToDownOnStairsUp', True)
        self.r.advance(1000)
        lit_steps = [name for _, name, on in self.r.writes if on and name.startswith('stairs_up_step')]
        self.assertEqual(lit_steps, ['stairs_up_step1', 'stairs_up_step2', 'stairs_up_step3'])

    def test_configured_hold_and_animation_speed(self):
        self.r.numbers['delay_after_animation_s'] = 5
        self.r.numbers['animation_delay_up_to_down_ms'] = 1000
        self.r.sensor(13, True)
        self.r.sensor(13, False)
        self.r.advance(16000)
        self.all_lit()
        self.r.advance(4000)
        self.all_lit()
        self.r.advance(1100)
        self.all_dark()

    def test_partitions_cover_all_pixels_exactly_once(self):
        indices = [i for light in CONFIG['light'] if light['platform'] == 'partition'
                   for segment in light['segments'] for i in range(segment['from'], segment['to'] + 1)]
        self.assertEqual(sorted(indices), list(range(len(self.r.pixels))))


if __name__ == '__main__':
    unittest.main()
