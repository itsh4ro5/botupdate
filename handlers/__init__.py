"""
Handlers package - Modularized Telegram Bot Handlers
100% backward compatible with `import handlers as H`
"""

from .common import _SYNC_IN_PROGRESS, _BOT_SELF_ID, _patch_rich_text_classes
from .common import *
from .broadcast import *
from .userbot import *
from .economy import *
from .batches import *
from .menus import *
from .admin import *
from .wizards import *
from .support import *
from .callbacks import *
