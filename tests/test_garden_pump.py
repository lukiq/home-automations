"""Replay the saved HA pump sequence and its cancellation contract in virtual time.

This tests the configuration, not Home Assistant's implementation or Tuya delivery.
Native ESPHome valve/button checks live in tests/garden/run.py.
"""
import heapq
import itertools
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPRINKLERS = 'switch.garden_house_controller_lawn_sprinklers'
PUMP = 'switch.tuyasmartplug_1'


class PumpReplay:
    def __init__(self, config):
        self.config = config
        self.now = 0
        self.state = 'off'
        self.token = None
        self.queue = []
        self.sequence = itertools.count()
        self.writes = []

    def condition(self, item):
        assert item['condition'] == 'state' and item['entity_id'] == SPRINKLERS
        return self.state == item['state']

    def actions(self, actions):
        for action in actions:
            if 'choose' in action:
                branch = next((choice['sequence'] for choice in action['choose']
                               if all(self.condition(c) for c in choice['conditions'])),
                              action.get('default', []))
                yield from self.actions(branch)
            elif 'delay' in action:
                yield action['delay']['seconds']
            elif 'condition' in action:
                if not self.condition(action):
                    return
            else:
                assert action['target']['entity_id'] == PUMP
                assert action['action'] in ('switch.turn_on', 'switch.turn_off')
                self.writes.append((self.now, action['action'] == 'switch.turn_on'))

    def resume(self, token, generator):
        if token is not self.token:
            return
        try:
            wait = next(generator)
        except StopIteration:
            self.token = None
            return
        heapq.heappush(self.queue, (self.now + wait, next(self.sequence), token, generator))

    def change(self, state):
        if state == self.state:
            return
        self.state = state
        if not any(t['entity_id'] == SPRINKLERS and t['to'] == state
                   for t in self.config['triggers']):
            return
        if self.token is not None and self.config['mode'] == 'single':
            return
        assert self.config['mode'] in ('single', 'restart')
        self.token = object()
        self.resume(self.token, self.actions(self.config['actions']))

    def advance(self, seconds):
        end = self.now + seconds
        while self.queue and self.queue[0][0] <= end:
            self.now, _, token, generator = heapq.heappop(self.queue)
            self.resume(token, generator)
        self.now = end


class GardenPump(unittest.TestCase):
    def setUp(self):
        self.config, = yaml.safe_load((ROOT / 'home-assistant/automations/garden-pump.yaml').read_text())
        self.replay = PumpReplay(self.config)

    def test_uninterrupted_start_preserves_power_cycle(self):
        r = self.replay
        r.change('on')
        r.advance(16.9)
        self.assertEqual(r.writes, [(2, False)])
        r.advance(0.2)
        self.assertEqual(r.writes, [(2, False), (17, True)])

    def test_stop_at_each_delay_and_after_start_cancels_future_on(self):
        for stop_time in (0.1, 1.9, 2.1, 5, 16.9, 17.1):
            with self.subTest(stop_time=stop_time):
                r = PumpReplay(self.config)
                r.change('on')
                r.advance(stop_time)
                r.change('off')
                count = len(r.writes)
                self.assertEqual(r.writes[-1], (stop_time, False))
                r.advance(30)
                self.assertEqual(len(r.writes), count)

    def test_restart_requires_a_new_complete_delay(self):
        r = self.replay
        r.change('on')
        r.advance(5)
        r.change('off')
        r.advance(1)
        r.change('on')
        r.advance(16)
        self.assertFalse(any(on for _, on in r.writes))
        r.advance(1.1)
        self.assertEqual(r.writes[-1], (23, True))

    def test_unavailable_state_blocks_the_final_on(self):
        r = self.replay
        r.change('on')
        r.advance(5)
        r.change('unavailable')
        r.advance(20)
        self.assertEqual(r.writes, [(2, False)])


if __name__ == '__main__':
    unittest.main()
