# Garden pump automation

`automations/garden-pump.yaml` is a replacement for the existing pump-start
automation, not an additional copy to run alongside it.

The ESP32 `main_section` output opens the main solenoid valve. Electrical power
for the pump is controlled separately by `switch.tuyasmartplug_1` in Home Assistant.

The pump sometimes stalls and recovers after a power cycle. The replacement
preserves the existing 2-second delay, power OFF, 15-second delay, power ON sequence. It handles both sprinkler ON and OFF in `mode: restart`:
STOP cancels pending delayed actions and sends power OFF. A final state check
prevents power ON when the sprinkler entity is no longer ON.

Deployment and rollback reference:

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

Deployment (2026-10-20): the existing start automation was replaced and remains
enabled; the old separate STOP automation was disabled. The saved YAML was read
back after reload and matched this definition, excluding the ID omitted by the
editor. No actions were run. TuyaSmartPlug_1 was unavailable in HA for about
20 hours, so actual pump power and recovery could not be verified. The
disconnected-load acceptance checks above remain pending. To roll back, restore
the previous start definition and re-enable the previous STOP automation together.
