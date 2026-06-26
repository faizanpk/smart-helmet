# speaker_mode.py – Routes audio output based on the loudspeaker toggle switch.
#
# Switch ON  → loudspeaker mode (helmet removed, audio plays through external speaker)
# Switch OFF → normal mode (helmet worn, audio plays through internal speaker)

import logging
import config
import gpio_handler as gpio

log = logging.getLogger(__name__)


def is_loudspeaker_mode() -> bool:
    """Returns True if the loudspeaker toggle switch is currently ON."""
    if gpio.IS_PI:
        return gpio.is_pressed(config.SWITCH_LOUDSPEAKER)
    return False   # laptops: always use system default, no physical switch


def get_speaker_device() -> str:
    """Returns the correct ALSA device name for the CURRENT switch position."""
    if is_loudspeaker_mode():
        return config.ALSA_SPK_DEVICE_EXTERNAL
    return config.ALSA_SPK_DEVICE_INTERNAL