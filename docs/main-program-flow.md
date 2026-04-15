# C.O.D.A Main Program Flow

## Purpose

This document explains how the runtime in main.py initializes, switches modes, handles input, and updates dashboard state.

## High-Level Flow

1. Parse CLI flags and optional microphone overrides.
2. Optionally list microphones and exit.
3. Determine startup mode (manual or voice).
4. Check for updates.
5. Load command cache and wakewords, or run first-time setup.
6. Start heartbeat thread for dashboard liveness.
7. Start voice thread if voice mode is active.
8. Enter the main loop.
9. On exit, stop threads and terminate.

## Detailed Flow

### 1) Startup and Arguments

- Read flags with \_get_flag_value and \_is_manual_mode_requested.
- If --list-mics is present:
    - Print available microphones.
    - Exit process.
- Set manual_assisstant_input when -m or --manual is passed.

### 2) Command and Wakeword Initialization

- Define default wakewords in code.
- Run check_update_available(version_url).
- If update is accepted:
    - Run update flow.
    - Rebuild commands cache with setup_commands().
- Else:
    - Try load_commands() and load_wakewords().
    - If files are missing, run run_first_time_setup().

### 3) Background Threads

- Start heartbeat thread with start_heartbeat_thread().
- Heartbeat loop calls dashboard_state.touch_heartbeat(source="main") on an interval.
- Start voice thread only when voice mode is active.

### 4) Main Loop Behavior

#### Manual Mode Branch

- Prompt with manual> and read input.
- Ignore empty input.
- If input is quit or exit:
    - Stop voice and heartbeat threads.
    - Exit loop.
- If input is voice or /voice:
    - Switch to voice mode.
    - Start voice thread.
- Require wakeword in manual command text.
- Strip content before wakeword.
- If command text remains, route it to command.run(...).

#### Voice Mode Branch

- Voice recognition runs in background thread.
- Main loop polls Ctrl+B toggle:
    - Switches to manual mode.
    - Stops voice thread.
    - Prints manual instructions.
- If keyboard hook fails, disable keyboard toggle and continue.

### 5) Shutdown

- On explicit exit command:
    - stop_voice_thread()
    - stop_heartbeat_thread()
- Break loop and terminate process.

## Dashboard State Touchpoints

- Heartbeat updates: main heartbeat loop -> dashboard_state.touch_heartbeat.
- User speech updates: voice_recognizer -> dashboard_state.record_user_message.
- AI response updates: speak_response -> dashboard_state.record_ai_response.
- Dashboard UI reads snapshot via Flask endpoint /api/state.

## Notes

- Commands cache is validated against command files and rebuilt when stale.
- Heartbeat thread is independent of manual or voice mode.
- Manual and voice inputs converge into the same command router.
