"""dmgbuild settings for the ClickAway disk image, used by scripts/build.py.

dmgbuild executes this file with a `defines` dict, which carries the paths from the
build script. Icon positions must match the artwork in scripts/make_dmg_background.py.
"""

from pathlib import Path

_defines = defines  # noqa: F821 - injected by dmgbuild; there is no __file__ here.
application = _defines["app"]
background = _defines["background"]

# A drag-and-drop window: the app on the left, an Applications alias on the right.
files = [application]
symlinks = {"Applications": "/Applications"}
hide_extension = [Path(application).name]
icon_locations = {Path(application).name: (160, 190), "Applications": (480, 190)}

format = "UDZO"
window_rect = ((240, 180), (640, 400))
default_view = "icon-view"
icon_size = 128
text_size = 13
label_pos = "bottom"
arrange_by = None
grid_spacing = 100
scroll_position = (0, 0)
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
include_icon_view_settings = True
include_list_view_settings = False
