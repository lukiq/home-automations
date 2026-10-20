# Garden pump automation

`automations/garden-pump.yaml` is a replacement for the existing pump-start
automation, not an additional copy to run alongside it. It has not been deployed.

The ESP32 `main_section` output opens the main solenoid valve. Electrical power
for the pump is controlled separately by `switch.tuyasmartplug_1` in Home Assistant.

The replacement preserves the existing 2-second delay, power OFF, 15-second delay,
power ON sequence. It handles both sprinkler ON and OFF in `mode: restart`:
STOP cancels pending delayed actions and sends power OFF. A final state check
prevents power ON when the sprinkler entity is no longer ON.

When a deployment is agreed:

1. Keep a copy of both existing pump automations.
2. Replace the definition of `garden_pump_on_sprinklers_start` with the list item's
   contents, retaining its existing ID. Do not create a second start automation.
3. Disable the old `garden_pump_off_sprinklers_stop` automation so that one
   automation owns both ON and OFF. Verify the entity IDs match the installation.
4. With the pump load disconnected, test an uninterrupted start, STOP during
   each delay, and start-STOP-start. After STOP, wait beyond the original
   17-second deadline and check that power does not return.

Configuration tests replay the YAML in virtual time. They do not execute Home
Assistant, confirm service delivery to Tuya, or establish actual pump power.
