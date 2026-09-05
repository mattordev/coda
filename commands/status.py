import platform
import json
import psutil
import os
import time
import math
import re
import utils.runtime_state as runtime_state

from ai.intents import Intent, IntentRequest
import utils.speak_response as speak


INTENT = Intent(
    name="status",
    description="Report information about the system running CODA.",
    aliases=(
        "system status",
        "system information",
        "system report",
        "coda status",
        "program status",
    ),
)

# TODO: have the program ask if the user wants to know about the system status, or the program status.
# Also implement someway to check the system health (temp, power usage etc.)


def run(request: IntentRequest) -> bool:
    """Generates a system or program report."""
    report_type = choose_report_type(request.message)

    if report_type is None:
        speak.speak_response(
            "Would you like a system report or a CODA status report?"
        )
        speak.speak_response("Try: 'CODA system status' or 'CODA program status'.")
        return True

    if report_type == "system":
        get_system_status()
    else:
        get_program_status()

    return True

def choose_report_type(message: str) -> str | None:
    """Determine which type of status report was requested"""
    words = message.lower().split()
    
    if "system" in words:
        return "system"
    
    if "coda" in words or "program" in words:
        return "coda"
    
    return None

# Gets the status of the machine or system that CODA is running on.
def get_windows_processor_name() -> str | None:
    """Read the processor model name from the Windows registry."""
    try:
        import winreg

        registry_path = (
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        )

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            registry_path,
        ) as processor_key:
            processor_name, _ = winreg.QueryValueEx(
                processor_key,
                "ProcessorNameString",
            )
    except (ImportError, OSError):
        return None

    return str(processor_name).strip() or None


def get_linux_processor_name() -> str | None:
    """Read the processor model name from Linux CPU information."""
    processor_fields = {}

    try:
        with open("/proc/cpuinfo", encoding="utf-8") as cpu_information:
            for line in cpu_information:
                field_name, separator, value = line.partition(":")

                if not separator:
                    continue

                normalized_name = field_name.strip().lower()
                normalized_value = value.strip()

                if normalized_value:
                    processor_fields.setdefault(
                        normalized_name,
                        normalized_value,
                    )
    except OSError:
        return None

    return (
        processor_fields.get("model name")
        or processor_fields.get("hardware")
        or processor_fields.get("model")
    )


def get_processor_name() -> str:
    """Return a friendly processor name for supported operating systems."""
    operating_system = platform.system()
    processor_name = None

    if operating_system == "Windows":
        processor_name = get_windows_processor_name()
    elif operating_system == "Linux":
        processor_name = get_linux_processor_name()

    return (
        processor_name
        or platform.processor()
        or platform.machine()
        or "Unknown"
    )


def format_processor_name_for_speech(processor_name: str) -> str:
    """Remove processor-name formatting that sounds unnatural aloud."""
    replacements = (
        ("(R)", ""),
        ("(TM)", ""),
        (" Gen ", " generation "),
        ("@", "at"),
        ("GHz", " gigahertz"),
        ("-", " "),
    )

    spoken_name = processor_name

    for original, replacement in replacements:
        spoken_name = spoken_name.replace(original, replacement)

    spoken_name = re.sub(
        r"\b(\d+\.\d+)\s+gigahertz\b",
        lambda match: f"{float(match.group(1)):g} gigahertz",
        spoken_name,
    )

    return " ".join(spoken_name.split())


def get_system_ram_usage() -> dict[str, float] | None:
    """Return total_gb, used_gb and percent for system memory."""
    memory = psutil.virtual_memory()
    
    # convert to GB
    total_gb = round(memory.total / (1024 ** 3), 1)
    # get used GB
    used_gb = round((memory.total - memory.available) / (1024 ** 3), 1)
    return {
        "total_gb": total_gb,
        "used_gb": used_gb,
        "percent": memory.percent,
    }


def get_battery_status() -> dict[str, object]:
    """Return battery percentage, time remaining and charging state."""
    battery = psutil.sensors_battery()
    
    if battery is None:
        return {
            "available": False,
            "reason": "No battery found/No battery information available."
        }
    
    return {
        "available": True,
        "percent": battery.percent,
        "seconds_left": battery.secsleft,
        "plugged_in": battery.power_plugged,
    }


def get_system_uptime() -> str:
    """Return the time since the operating system started."""
    calculated_time = time.time() - psutil.boot_time()
    return format_uptime(calculated_time)


def get_system_status_data() -> dict[str, object]:
    """Collect the values used in the system status report."""
    architecture, executable_format = platform.architecture()

    return {
        "operating_system": platform.system(),
        "processor": get_processor_name(),
        "architecture": architecture,
        "executable_format": executable_format,
        "machine": platform.machine(),
        "ram_usage": get_system_ram_usage(),
        "battery": get_battery_status(),
        "system_uptime": get_system_uptime(),
    }


def print_system_status(status: dict[str, object]) -> None:
    """Print and speak a collected system status report."""
    ram_usage = status["ram_usage"]
    battery = status["battery"]
    system_uptime = status["system_uptime"] or "Unavailable"

    if isinstance(ram_usage, dict):
        ram_status = (
            f"{ram_usage['used_gb']} GB of {ram_usage['total_gb']} GB used "
            f"({ram_usage['percent']} percent)"
        )
    else:
        ram_status = "Unavailable"

    remaining_battery_time = None

    if battery["available"]:
        charging_status = (
            "charging" if battery["plugged_in"] else "not charging"
        )
        battery_status = f"{battery['percent']}%, {charging_status}"

        seconds_left = battery.get("seconds_left")
        if (
            not battery["plugged_in"]
            and isinstance(seconds_left, (int, float))
            and seconds_left >= 0
        ):
            remaining_battery_time = format_uptime(seconds_left)
            battery_status += f", {remaining_battery_time} remaining"
    else:
        battery_status = str(battery["reason"])

    print(f"This system is running on: {status['operating_system']}")

    print(f"This system has a: {status['processor']} processor.")

    print(
        f"This system architecture is: {status['architecture']} "
        f"({status['executable_format']})"
    )

    print(f"This system machine type is: {status['machine']}")
    print(f"System RAM usage is: {ram_status}")
    print(f"System uptime is: {system_uptime}")
    print(f"Battery status is: {battery_status}")

    spoken_processor = format_processor_name_for_speech(status["processor"])
    spoken_architecture = status["architecture"].replace("bit", " bit")
    spoken_details = []

    if isinstance(ram_usage, dict):
        spoken_details.append(
            f"System memory usage is {ram_usage['percent']} percent."
        )

    if status["system_uptime"]:
        spoken_details.append(
            f"System uptime is {status['system_uptime']}."
        )

    if isinstance(battery, dict) and battery.get("available"):
        spoken_battery_status = (
            f"The battery is at {battery['percent']} percent and is "
            f"{charging_status}"
        )

        if remaining_battery_time:
            spoken_battery_status += (
                f", with approximately {remaining_battery_time} remaining"
            )

        spoken_details.append(f"{spoken_battery_status}.")
    elif isinstance(battery, dict):
        spoken_details.append(str(battery.get("reason", "Battery unavailable.")))

    spoken_status_details = " ".join(spoken_details)

    spoken_summary = (
        f"This system is running {status['operating_system']}. "
        f"The processor is an {spoken_processor}, and it uses a "
        f"{spoken_architecture} architecture. "
        f"{spoken_status_details}"
    ).strip()

    speak.speak_response(spoken_summary)

    print(
        f"""
        ----SYSTEM REPORT----
        - Operating System: {status['operating_system']}
        - Processor: {status['processor']}
        - Architecture: {status['architecture']}
        - Executable Format: {status['executable_format']}
        - Machine Type: {status['machine']}
        - RAM Usage: {ram_status}
        - System Uptime: {system_uptime}
        - Battery: {battery_status}
        """
    )


def get_system_status() -> None:
    """Collect and present the current system status."""
    print("Checking system status...")
    status = get_system_status_data()
    print_system_status(status)

# Gets the status of the program.


def get_program_version() -> str:
    with open("version.json", encoding="utf-8") as version_file:
        data = json.load(version_file)
        
    return data["version"]

def get_used_memory() -> int:
    process = psutil.Process()
    memory_in_bytes = process.memory_info().rss
    # convert bytes to MB:
    memory_in_megabytes = (memory_in_bytes / 1000000)
    return math.trunc(memory_in_megabytes)

def get_uptime() -> str:
    process = psutil.Process(os.getpid())
    start_time = process.create_time()
    current_time = time.time()
    calculated_time = current_time - start_time
    return format_uptime(calculated_time)

def format_uptime(total_seconds: float) -> str:
    """Convert seconds into a readable duration."""
    remaining_seconds = int(total_seconds)

    days, remaining_seconds = divmod(remaining_seconds, 86400)
    hours, remaining_seconds = divmod(remaining_seconds, 3600)
    minutes, seconds = divmod(remaining_seconds, 60)

    parts = []

    if days:
        unit = "day" if days == 1 else "days"
        parts.append(f"{days} {unit}")

    if hours:
        unit = "hour" if hours == 1 else "hours"
        parts.append(f"{hours} {unit}")

    if minutes:
        unit = "minute" if minutes == 1 else "minutes"
        parts.append(f"{minutes} {unit}")

    if seconds or not parts:
        unit = "second" if seconds == 1 else "seconds"
        parts.append(f"{seconds} {unit}")

    if len(parts) == 1:
        return parts[0]

    return ", ".join(parts[:-1]) + " and " + parts[-1]

def is_debug_enabled() -> bool:
    return runtime_state.is_debug_enabled()

def get_input_mode() -> str:
    return runtime_state.get_input_mode()
    
def get_stt_status() -> str:
    return "Configured - We are using faster-whisper"


def get_program_status_data() -> dict[str, object]:
    """Collect the values used in CODA's program status report."""
    return {
        "version": get_program_version(),
        "memory_mb": get_used_memory(),
        "uptime": get_uptime(),
        "debug": is_debug_enabled(),
        "input_mode": get_input_mode(),
        "stt_provider": get_stt_status(),
        "tts_available": speak.is_tts_available(),
    }


def print_program_status(status: dict[str, object]) -> None:
    """Print and speak a collected CODA program status report."""
    debug_status = "ON" if status["debug"] else "OFF"
    debug_spoken_status = debug_status.lower()
    tts_status = "Available" if status["tts_available"] else "Unavailable"

    print(f"We are running version {status['version']}.")
    speak.speak_response(f"We are running version {status['version']}.")

    print(f"We are using {status['memory_mb']}MB of memory.")
    speak.speak_response(
        f"We are using {status['memory_mb']} megabytes of memory."
    )

    print(f"The current uptime is {status['uptime']}.")
    speak.speak_response(f"The current uptime is {status['uptime']}.")

    print(f"Debug mode is currently {debug_status}.")
    speak.speak_response(
        f"Debug mode is currently {debug_spoken_status}."
    )

    print(f"The current input mode is {status['input_mode']}.")
    speak.speak_response(
        f"The current input mode is {status['input_mode']}."
    )

    print(f"STT (Speech-To-Text) is {status['stt_provider']}")
    speak.speak_response(
        f"Speech To Text is {status['stt_provider']}"
    )

    print(f"TTS (Text-To-Speech) is {tts_status}")
    speak.speak_response(f"Text-To-Speech is {tts_status}")

    print(
        f"""
        ----CODA REPORT----
        - CODA Version: {status['version']}
        - Used Memory: {status['memory_mb']}MB
        - Current Uptime: {status['uptime']}
        - Debug Mode (ON/OFF): {debug_status}
        - Input Mode: {status['input_mode']}
        - STT (Speech-To-Text): {status['stt_provider']}
        - TTS (Text-To-Speech): {tts_status}
        """
    )


def get_program_status() -> None:
    """Collect and present CODA's current program status."""
    print("Checking CODA status...")
    status = get_program_status_data()
    print_program_status(status)
