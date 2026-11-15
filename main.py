"""RAT (Radical Advanced Tools): Decky Loader backend. Thin async wrappers around rat.installer.Manager and its
services (ReShade, RenoDX, profiles)."""
import asyncio
import functools
import os
import sys
import traceback

import decky  # provided by Decky Loader

# py_modules is on sys.path for Decky plugins; make sure when loaded another way (tests, older loaders)
_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.join(_HERE, "py_modules") not in sys.path:
    sys.path.insert(0, os.path.join(_HERE, "py_modules"))

from rat.installer import Manager  # noqa: E402
from rat.util import Paths, UserError  # noqa: E402


def _result(fn):
    """Runs a blocking Manager call in a worker thread and returns {ok, data} or {ok: False, error}, so the
    frontend always gets a readable message instead of an exception."""
    @functools.wraps(fn)
    async def wrapper(self, *args):
        try:
            data = await asyncio.get_running_loop().run_in_executor(None, lambda: fn(self, *args))
            return {"ok": True, "data": data}
        except UserError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            decky.logger.error("%s failed: %s", fn.__name__, traceback.format_exc())
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return wrapper


def _idle(fn):
    """For calls that change game files or records: refused while a background job (install, update, ReShade or HDR
    setup...) runs, so two writers never edit the same files or records at once."""
    @functools.wraps(fn)
    def wrapper(self, *args):
        if self.manager.jobs.busy():
            raise UserError("Busy: wait for the running task to finish, then try again.")
        return fn(self, *args)
    return wrapper


class Plugin:
    manager: Manager = None

    async def _main(self):
        Paths.configure(decky.DECKY_USER_HOME, decky.DECKY_PLUGIN_DIR, decky.DECKY_PLUGIN_SETTINGS_DIR,
                        decky.DECKY_PLUGIN_RUNTIME_DIR)
        import logging
        logging.getLogger("rat").handlers = decky.logger.handlers
        logging.getLogger("rat").setLevel(logging.INFO)
        Plugin.manager = Manager()
        decky.logger.info("RAT (Radical Advanced Tools) %s ready (user %s)", decky.DECKY_PLUGIN_VERSION, decky.DECKY_USER)

    async def _unload(self):
        if Plugin.manager:
            Plugin.manager.jobs.cancel()

    async def _uninstall(self):
        # Game installs are left alone on purpose: they are removed per game in the plugin (restoring the game's
        # files). Downloaded packages, ReShade binaries and shaders are RAT's own and go with it.
        import shutil
        for d in ("packages", "downloads", "reshade", "renodx", "cache"):
            shutil.rmtree(os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, d), ignore_errors=True)

    # --- overview and games ---------------------------------------------------------------------------------------

    @_result
    def overview(self):
        return self.manager.overview()

    @_result
    def list_games(self, refresh=False):
        return self.manager.games(bool(refresh))

    @_result
    def game_detail(self, key):
        return self.manager.detail(str(key))

    @_result
    def set_exe_dir(self, key, path):
        self.manager.set_exe_dir(str(key), path)
        return self.manager.detail(str(key))

    @_result
    def add_custom_game(self, folder):
        return self.manager.add_custom_game(folder)

    @_result
    def remove_custom_game(self, key):
        self.manager.remove_custom_game(str(key))
        return True

    @_result
    def set_choice(self, key, source_id=None, upscaler=None, option=None):
        self.manager.set_choice(str(key), source_id, upscaler, option)
        return self.manager.detail(str(key))

    @_result
    def set_pref(self, name, value):
        return self.manager.set_pref(name, value)

    # --- add-on sources -------------------------------------------------------------------------------------------

    @_result
    def check_updates(self, source_id, force=False):
        return self.manager.check_updates(source_id, bool(force))

    @_result
    def download_source(self, source_id):
        return self.manager.start_download(source_id)

    @_result
    def import_link(self, url):
        return self.manager.import_link(url)

    @_result
    def import_file(self, path):
        return self.manager.import_file(path)

    @_result
    def remove_source(self, source_id):
        self.manager.remove_source(source_id)
        return True

    # --- per game injector ----------------------------------------------------------------------------------------

    @_result
    def install(self, key, force=False):
        return self.manager.start_install(str(key), bool(force))

    @_result
    def update(self, key):
        return self.manager.start_update(str(key))

    @_result
    def uninstall(self, key):
        return self.manager.start_uninstall(str(key))

    @_result
    @_idle
    def set_active(self, key, active):
        return self.manager.set_active(str(key), bool(active))

    @_result
    @_idle
    def set_upscaler(self, key, upscaler):
        return self.manager.set_upscaler(str(key), upscaler)

    @_result
    @_idle
    def set_mode(self, key, path):
        return self.manager.set_mode(str(key), str(path))

    @_result
    @_idle
    def set_option(self, key, option_id, value):
        return self.manager.set_option(str(key), option_id, value)

    @_result
    def launch_plan(self, key, current=None, strip_foreign=False):
        return self.manager.launch_plan(str(key), current, bool(strip_foreign))

    @_result
    def set_preserve_swapchain(self, key, on):
        return self.manager.set_preserve_swapchain(str(key), bool(on))

    @_result
    def launch_applied(self, key, before=None):
        self.manager.launch_applied(str(key), before)
        return True

    @_result
    def launch_removal(self, key, part, current=None):
        return self.manager.launch_removal(str(key), str(part), current)

    @_result
    def launch_keep(self, key, part, current=None):
        return self.manager.launch_keep(str(key), str(part), current)

    @_result
    def read_configs(self, key):
        return self.manager.read_configs(str(key))

    @_result
    def write_config_value(self, key, path, section, name, value):
        return self.manager.write_config_value(str(key), path, section, name, value)

    @_result
    def wipe_external(self, key):
        return self.manager.start_wipe_external(str(key))

    # --- injector settings and frame generation --------------------------------------------------------------------

    @_result
    def ini_files(self, key):
        return self.manager.ini_files(str(key))

    @_result
    def ini_essentials(self, key):
        return self.manager.ini_essentials(str(key))

    @_result
    def ini_section(self, key, file_id, section):
        return self.manager.ini_section(str(key), str(file_id), str(section))

    @_result
    @_idle
    def save_ini(self, key, file_id, edits):
        return self.manager.save_ini(str(key), str(file_id), edits)

    @_result
    def frame_gen(self, key):
        return self.manager.frame_gen(str(key))

    @_result
    def fg_fallback_install(self, key, groups):
        return self.manager.start_fg_fallback(str(key), [str(g) for g in (groups or [])])

    @_result
    @_idle
    def fg_fallback_remove(self, key):
        return self.manager.remove_fg_fallback(str(key))

    # --- launch status / boot HUD ---------------------------------------------------------------------------------

    @_result
    def launch_status(self, key):
        return self.manager.launch_status(str(key))

    @_result
    def game_started(self, appid):
        return self.manager.game_started(appid)

    @_result
    def game_stopped(self, appid):
        self.manager.game_stopped(appid)
        return True

    # --- ReShade --------------------------------------------------------------------------------------------------

    @_result
    @_idle
    def reshade_enable(self, key, on):
        return self.manager.reshade.set_enabled(str(key), bool(on))

    @_result
    def reshade_params(self, key, file):
        return self.manager.reshade.effect_params(str(key), str(file))

    @_result
    def reshade_set_param(self, key, file, name, values):
        return self.manager.reshade.set_param(str(key), str(file), str(name), values)

    @_result
    def reshade_param_defaults(self, key, file, action):
        if action == "save":
            return self.manager.reshade.save_param_defaults(str(key), str(file))
        return self.manager.reshade.restore_param_defaults(str(key), str(file))

    @_result
    def reshade_state(self, key):
        return self.manager.reshade.game_state(str(key))

    @_result
    def reshade_install(self, key, addon=True, proxy="auto"):
        return self.manager.reshade.start_install_game(str(key), bool(addon), str(proxy or "auto"))

    @_result
    @_idle
    def reshade_uninstall(self, key):
        return self.manager.reshade.uninstall_game(str(key), by="user")

    @_result
    def reshade_packages(self, force=False):
        return self.manager.reshade.packages(bool(force))

    @_result
    def reshade_install_packages(self, keys):
        return self.manager.reshade.start_install_packages([str(k) for k in keys or []])

    @_result
    def reshade_remove_package(self, key):
        self.manager.reshade.remove_package(str(key))
        return self.manager.reshade.packages()

    @_result
    def reshade_presets(self, key):
        return self.manager.reshade.presets(str(key))

    @_result
    @_idle
    def reshade_preset_action(self, key, action, name="", arg=""):
        return self.manager.reshade.preset_action(str(key), str(action), str(name or ""), str(arg or ""))

    @_result
    def reshade_effects(self, key):
        return self.manager.reshade.preset_effects(str(key))

    @_result
    def reshade_toggle_effect(self, key, effect, enabled):
        return self.manager.reshade.toggle_effect(str(key), str(effect), bool(enabled))

    @_result
    def reshade_set_compositor(self, key, effect=None, apply_now=False):
        return self.manager.reshade.set_compositor(str(key), effect or None, bool(apply_now))

    # --- RenoDX HDR -----------------------------------------------------------------------------------------------

    @_result
    def hdr_state(self, key, force=False):
        return self.manager.renodx.game_state(str(key), bool(force))

    @_result
    def hdr_install(self, key, choice, url=None, path=None):
        return self.manager.renodx.start_install(str(key), str(choice), url, path)

    @_result
    @_idle
    def hdr_enable(self, key, on):
        return self.manager.renodx.set_enabled(str(key), bool(on))

    @_result
    @_idle
    def hdr_setting(self, key, name, value):
        return self.manager.renodx.set_setting(str(key), str(name), value)

    @_result
    def hdr_browse(self, force=False):
        return self.manager.renodx.browse_all(bool(force))

    @_result
    def hdr_export(self, key):
        return self.manager.renodx.export_settings(str(key))

    @_result
    def hdr_import(self, key, path):
        return self.manager.renodx.import_settings(str(key), str(path))

    @_result
    @_idle
    def hdr_uninstall(self, key):
        return self.manager.renodx.uninstall(str(key))

    @_result
    @_idle
    def hdr_autoconfig(self, key, force=False):
        return self.manager.renodx.autoconfigure(str(key), bool(force))

    # --- profiles -------------------------------------------------------------------------------------------------

    @_result
    def profile_copy(self, key):
        return self.manager.profiles.copy(str(key))

    @_result
    def profile_clipboard(self):
        return self.manager.profiles.clipboard()

    @_result
    def profile_paste(self, key):
        return self.manager.profiles.paste(str(key))

    @_result
    def profile_candidates(self, key):
        return self.manager.profiles.candidates(str(key))

    @_result
    def profile_from_game(self, key, source_key):
        return self.manager.profiles.apply_from_game(str(key), str(source_key))

    @_result
    def profile_export(self, key):
        return self.manager.profiles.export(str(key))

    @_result
    def profile_files(self):
        return self.manager.profiles.list_files()

    @_result
    def profile_import(self, key, path):
        return self.manager.profiles.import_file(str(key), str(path))

    @_result
    def profile_delete(self, key):
        return self.manager.profiles.delete(str(key))

    @_result
    def profile_delete_file(self, path):
        return self.manager.profiles.delete_file(str(path))

    # --- jobs -----------------------------------------------------------------------------------------------------

    @_result
    def get_job(self):
        return self.manager.jobs.get()

    @_result
    def cancel_job(self):
        return self.manager.jobs.cancel()
