# NeoSmartBlinds for Home Assistant

Control blinds connected to a [Neo Smart Blinds](https://neosmartblinds.com) controller from Home Assistant, over the controller's local HTTP or TCP API. Nothing goes through the cloud.

Version 3 is a rewrite. It is set up from the UI, and existing YAML configurations are imported automatically (see [Upgrading from 2.x](#upgrading-from-2x)).

## What you get

For each blind:

- a **cover** entity: open, close, stop, set position (modes 1 and 2) and, if you turn on **Supports tilt**, tilt (micro-step up and down);
- a **Favourite position 1** button, plus **Favourite position 2** for NEO (`no`) and Raex (`rx`) motors, or when no motor code is set. These are **disabled by default**, because the controller can't tell Home Assistant whether a favourite has been saved. Save one in the Neo app (the blind's advanced page → **Set Favorite 1/2**) or with the blind's remote, then enable the button from the blind's device page.

Each blind is its own device, linked to a device for the controller.

For each **room code** shared by two or more blinds, there is also a **room cover**. Name rooms from the controller's **Configure** button (Settings → Devices & services → NeoSmartBlinds → ⚙ on the controller); unnamed rooms are called *Room* plus the code. See [Room commands](#room-commands).

The controller device has a **Connection** sensor (under Diagnostics). It's on while the controller answers: it updates with every command, and Home Assistant also checks every 5 minutes that the controller accepts a connection, without sending a command.

The controller never reports where a blind is, so the position is always an estimate based on how long the blind takes to close. The cover is marked as an assumed state, so Home Assistant shows open and close buttons instead of a toggle.

## Installation

Give the controller a fixed IP address in your router first.

**HACS:** add `https://github.com/josh345rtfvqw/Home_Assistant_NeoSmartBlinds` as a custom repository (type: Integration), install **NeoSmart Blinds**, and restart Home Assistant.

**Manual:** copy `custom_components/neosmartblinds` into your `config/custom_components/` folder and restart Home Assistant.

Requires Home Assistant 2025.8 or newer.

## Setup

### 1. Add the controller

**Settings → Devices & services → Add integration → NeoSmartBlinds.** No YAML is needed.

The first form asks for:

| Field | |
|---|---|
| Host | The controller's IP address, from **Your Controllers** in the Neo app. |
| Protocol | `HTTP` (port 8838) or `TCP` (port 8839). HTTP is recommended: the controller reports a wrong controller ID or a bad command. TCP only echoes the command back. |

The second form has the port already filled in for the protocol you chose, plus:

| Field | |
|---|---|
| Port | Change it only if your controller uses a different port. |
| Controller ID | The 24 character ID under the controller in **Your Controllers** in the Neo app. Required for HTTP, not used by TCP. |

Home Assistant checks that it can open a connection to the controller. It does not send a command, so it cannot check the controller ID at this point. If the ID is wrong, the first command fails and Home Assistant asks you to re-enter it.

**Finding the IP address and controller ID in the Neo app:** open the menu (☰) and choose **Your Controllers**. Under each controller's name you'll see a long **ID** (about 24 characters, the controller ID) and an **IP** line. If you have more than one controller, use the one assigned to the room your blinds are in; a room's controller is shown under **Edit Room → Assign Smart Controller to room**.

### 2. Add blinds

On the controller's entry, choose **Add blind**.

| Field | |
|---|---|
| Name | Name shown in Home Assistant. |
| Blind code | From the blind's advanced page in the Neo app, in the form `ID1.ID2-CHANNEL`, for example `021.230-04`. Use the room code (channel `15`) to control a whole room as one cover. |
| Motor code | Shown under the blind code in the Neo app, for example `bf`. Pick it from the list (each code is shown with its motor name, such as `bf – Bofu (standard)`), or type it. Some controllers (such as the C-BR300) need it. |
| Rail | For top-down/bottom-up blinds: top/middle rail, bottom/lower rail, or both. Ordinary blinds use the top rail. |
| Extended top-down/bottom-up commands | Only for top-down/bottom-up blinds with `mb`, `ra`, `ap` or `db` motors. These use the newer `u4`/`d4`/`x2` command set; `tb` motors use the standard set. |
| Supports tilt | Turn on for blinds whose slats tilt. Adds the tilt open/close controls (micro-steps). Off by default. Some motors, such as Wistar/Alpha (`wt`), ignore micro-steps. |
| Positioning mode | See below. |
| Close time | Seconds the blind takes to go from fully open to fully closed. Measure it afterwards with **Measure open and close times** (below), or use a stopwatch. |
| Open time | Optional. Seconds from fully closed to fully open, if it differs from the close time (blinds often rise slower). Empty means the same as the close time. |
| Re-sync when Home Assistant starts | Don't move the blind (default), Open fully, or Close fully. See [Keeping the position accurate](#keeping-the-position-accurate). |
| Start position | Optional. The position to assume the first time, before Home Assistant has saved one (modes 1 and 2). After that the last known position is always restored. |
| Room code (parent group) | Optional. The room code (ending `-15`) shared by blinds in the same room. See [Room commands](#room-commands). |

**Finding the blind code, motor code and room code in the Neo app:** open the blind and go to its advanced page (**Advanced Controls**). Near the bottom it lists:

- **Room Code**, for example `242.210-15`: use it as the room code (parent group).
- **Blind Code**, for example `242.210-01`: use it as the blind code.
- **Motor code**, for example `bf`: pick it in the motor code list.

All blinds in the same room share the first part of the code (`242.210`); the last two digits are the blind's channel, and `15` means the whole room.

To change a blind or the controller later, use **Reconfigure** on it. A blind keeps its entity when you change its settings. To remove a blind, open its menu (⋮) on the controller's entry and choose **Delete**.

### Positioning modes

| Mode | Set position does | Use with |
|---|---|---|
| **0 – Favourites only** | No position. The blind shows up, stop and down buttons instead of a slider; use the favourite buttons for favourite positions. | Any motor. |
| **1 – Send position to the blind** | Sends the position (`01`–`99`) and the blind goes there by itself. | Motor codes `no`, `db`, `ra`, `rb`, `ap`, `bl`, `mb`, `jo` on the top rail. |
| **2 – Emulate position** | Moves up or down, then sends stop after the time calculated from the open and close times. | Any motor. Accuracy depends on those times. |

In every mode, position 0 and 100 (and anything below 2 or above 98) send a plain close or open, without a stop. While a blind is moving, its position is updated every second.

If you ask for a new position while the blind is moving, and the blind is already heading that way, the move is extended or shortened without stopping. If the new position is behind the blind, it stops and then moves back.

### Keeping the position accurate

The controller never reports where a blind is, so the position is an estimate. Home Assistant saves it and restores it after a restart.

- **Measure both times.** Open the blind's settings (Settings → Devices & services → NeoSmartBlinds → ⚙ next to the blind) and choose **Measure open and close times**. Home Assistant opens the blind, then times it closing and opening while you press **Submit** the moment it stops each time, and saves both times. You can also type them in yourself.
- **Full open and close re-sync.** Opening or closing fully always runs the motor to its end stop, so the position is exact afterwards.
- **Re-sync at startup.** The estimate drifts if the blind is moved with the Neo app or a remote. Set **Re-sync when Home Assistant starts** to *Open fully* or *Close fully* to move the blind to that end every time Home Assistant starts (not when the integration is merely reloaded). Remember this also happens after updates and restarts at night.

For HomeKit, use mode 1 or 2 so the position in the Home app makes sense.

### Room commands

The controller handles one command at a time, and needs about half a second for each. Moving five blinds one by one therefore takes a few seconds.

If several blinds have the same **room code**, and every one of them gets the same command within 250 ms (from a scene, an area, a HomeKit room or an automation), one room command is sent instead. If only some of them get the command, each is sent its own command after the 250 ms wait. Blinds without a room code are sent commands straight away.

The **room cover** uses this: opening, closing, stopping or positioning it sends the command to every blind in the room at once, so the controller gets a single room command and each blind still tracks its own position. Its position is the average of the blinds; it offers a slider only when every blind in the room uses positioning mode 1 or 2. No room cover is made if you've added the room code itself as a blind.

All commands to a controller are queued and spaced at least 0.7 s apart, as the controller's documentation asks.

### Sending other commands

> [!WARNING]
> **Use at your own risk.** Raw commands are sent to the motor exactly as given. They may do nothing on your blinds, and some Neo commands are *programming* commands that change the motor's settings, such as its limits, direction, favourites or pairing. A wrong one can leave a blind not working properly until your installer sets it up again. Only send commands you know your motor supports.

The **NeoSmartBlinds: Send command** action (`neosmartblinds.send_command`) sends any two-character Neo action to a blind, or to a whole room through its room cover. Use it for commands the cover doesn't offer, such as `i1`/`i2` (intermediate positions on NEO motors), `u4`/`d4` (middle rail on extended top-down/bottom-up blinds) or micro-steps on a blind without **Supports tilt**. The blind's motor code is added for you. See the [protocol reference](#protocol-reference) for the actions.

```yaml
action: neosmartblinds.send_command
target:
  entity_id: cover.front_blind
data:
  command: i1
```

Home Assistant can't tell what a raw command did, so the blind's estimated position isn't changed.

## Upgrading from 2.x

Your `cover: - platform: neosmartblinds` YAML entries are imported when Home Assistant starts:

1. Install 3.0.0 and restart.
2. A NeoSmartBlinds controller entry appears under **Settings → Devices & services**, with one blind per YAML entry. Blinds with the same `hub_id` (or host) end up under the same controller.
3. Your existing entities are kept, with the same entity IDs, history and customisations.
4. A repair notice asks you to remove the YAML. Remove the `neosmartblinds` entries from `configuration.yaml` and restart. After that, change settings from the UI.

If a YAML entry can't be imported (for example, a blind code in the wrong format), a repair notice says which one and why. Fix it in YAML and restart, or add the blind from the UI.

### What changed

- **The tilt slider no longer selects favourites.** Use the **Favourite position 1/2** buttons (new blinds get them disabled; enable them on the device page). Tilt open/close still send micro-steps, but only show when **Supports tilt** is turned on for the blind (it's off for imported blinds).
- Everything is set up from the UI. `protocol` is no longer case-sensitive, and the port is filled in for the protocol (8838 for HTTP, 8839 for TCP).
- `rail` accepts `3` (both rails), and there is an option for the extended top-down/bottom-up command set (`u4`/`d4`/`x2`).
- Errors from the controller are shown when a command fails, instead of only being logged. A rejected controller ID starts a re-authentication flow.
- **Mode 0 no longer has a position.** Setting the position to 50 or 51 to pick a favourite is gone; automations that did this should press the favourite buttons instead. The blind shows up, stop and down buttons, and remembers whether it was last opened or closed.
- The `requests` dependency is gone. The integration uses Home Assistant's own HTTP session.
- HACS installs from the repository instead of a release zip.

The old options map to the new form like this:

| YAML | UI |
|---|---|
| `host`, `hub_id`, `protocol`, `port` | Controller entry |
| `name`, `blind_code`, `motor_code`, `rail`, `percent_support`, `close_time`, `start_position`, `parent_group` | Blind (`start_position` now only applies before a position has been saved; re-sync and open time are set in the UI) |

## Protocol reference

Commands have the form `ID1.ID2-CHANNEL-ACTION!MOTORCODE`, for example `021.230-04-up!bf`. Over HTTP they are sent as `GET http://<host>:8838/neo/v1/transmit?command=…&id=<controller id>&hash=<7 digits>`. Over TCP, port 8839 receives the command plus CRLF and echoes it back.

| Action | Sent for |
|---|---|
| `up`, `dn`, `sp` | Open, close, stop (top rail) |
| `mu`, `md` | Tilt open/close (top rail) |
| `01`–`99` | Set position in mode 1. The value is percent *closed*, so Home Assistant 30 % open is sent as `70`. |
| `gp`, `i2` | Favourite 1, favourite 2 |
| `u2`, `d2`, `o2`, `c2` | Bottom rail: open, close, tilt open, tilt close |
| `u3`, `d3` | Both rails |
| `u4`, `d4`, `x2` | Extended top-down/bottom-up: middle rail open/close, stop |

## Development

```bash
python3.13 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements_test.txt
```

```bash
.venv/bin/pytest
```

The tests run a fake controller on localhost that speaks both HTTP and TCP.

### Releasing

Bump `version` in `custom_components/neosmartblinds/manifest.json` and push to `master`. Once the **Validate** workflow passes, the **Release** workflow tags `v<version>` and publishes a GitHub release listing the commits since the previous one; HACS then offers the update. Pushes that don't change the version don't make a release. You can edit the release notes on GitHub afterwards.

Dependabot opens a pull request when an action used by the workflows has a new version.

## Credits

Version 3 is maintained by [@josh345rtfvqw](https://github.com/josh345rtfvqw). It builds on the original integration by [@4n0nD3v3l0p3r](https://github.com/4n0nD3v3l0p3r/Home_Assistant_NeoSmartBlinds) and the 2.x fork by [@mtgeekman](https://github.com/mtgeekman/Home_Assistant_NeoSmartBlinds), with contributions from [@clara-j](https://github.com/clara-j) and [@gee-jay-bee](https://github.com/gee-jay-bee).

## License

[MIT](LICENSE).

## Reporting a problem

Open an [issue](https://github.com/josh345rtfvqw/Home_Assistant_NeoSmartBlinds/issues) and attach the diagnostics: Settings → Devices & services → NeoSmartBlinds → ⋮ on the controller → **Download diagnostics**. They include your blind settings, the connection status and the last 20 commands with their results; the controller's address and ID are removed.
