# -*- coding: utf-8 -*-
"""Tests de es-de-launch (unittest, bibliothèque standard).

Lancement :  python3 -m unittest discover -s tests -v
Les fixtures es_systems.xml / es_find_rules.xml sont les fichiers réels
extraits de l'AppImage ES-DE de l'installation de référence.
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shlex
import shutil
import stat
import struct
import sys
import tempfile
import time
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "es-de-launch")
FIXTURES = os.path.join(HERE, "fixtures")


def load_module():
    loader = importlib.machinery.SourceFileLoader("esdelaunch", SCRIPT)
    spec = importlib.util.spec_from_loader("esdelaunch", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


L = load_module()


# --------------------------------------------------------------------------
# Fabrication de fichiers de test
# --------------------------------------------------------------------------

def write(path, content, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = content.encode() if isinstance(content, str) else content
    with open(path, "wb") as fh:
        fh.write(data)
    if mode:
        os.chmod(path, mode)
    return path


def n64_rom(order="z64"):
    magic = {"z64": b"\x80\x37\x12\x40", "v64": b"\x37\x80\x40\x12", "n64": b"\x40\x12\x37\x80"}[order]
    return magic + b"\x00" * 0x1000


def gba_rom():
    data = bytearray(0x200)
    data[4:12] = L.GBA_LOGO
    data[0xB2] = 0x96
    return bytes(data)


def gc_disc():
    data = bytearray(0x8000)
    data[0x1C:0x20] = b"\xc2\x33\x9f\x3d"
    return bytes(data)


def iso_image(files, system_id=""):
    """ISO9660 minimal : PVD au secteur 16, racine au 18, fichiers à partir du 19."""
    sec = 2048
    img = bytearray(sec * (20 + len(files)))

    def dir_record(name, lba, size, is_dir=False):
        name_b = name.encode()
        ln = 33 + len(name_b) + (1 if len(name_b) % 2 == 0 else 0)
        rec = bytearray(ln)
        rec[0] = ln
        rec[2:6] = struct.pack("<I", lba)
        rec[6:10] = struct.pack(">I", lba)
        rec[10:14] = struct.pack("<I", size)
        rec[14:18] = struct.pack(">I", size)
        rec[25] = 2 if is_dir else 0
        rec[32] = len(name_b)
        rec[33:33 + len(name_b)] = name_b
        return bytes(rec)

    pvd = bytearray(sec)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    pvd[8:40] = system_id.encode().ljust(32)
    root = dir_record("\x00", 18, sec, True)
    pvd[156:156 + len(root)] = root
    img[16 * sec:17 * sec] = pvd
    entries = dir_record("\x00", 18, sec, True) + dir_record("\x01", 18, sec, True)
    for i, (name, content) in enumerate(files.items()):
        lba = 19 + i
        entries += dir_record(name + ";1", lba, len(content))
        img[lba * sec:lba * sec + len(content)] = content
    img[18 * sec:18 * sec + len(entries)] = entries
    return bytes(img)


def iso_with_system_cnf(cnf_text):
    return iso_image({"SYSTEM.CNF": cnf_text.encode()})


def make_cso(data, bs=2048):
    """Compresse une image en PSP .cso (CISO v1, deflate brut)."""
    import zlib as _z
    n = (len(data) + bs - 1) // bs
    header = b"CISO" + struct.pack("<IQIBB2x", 0x18, len(data), bs, 1, 0)
    pos = 24 + 4 * (n + 1)
    index, blocks = [], b""
    for i in range(n):
        c = _z.compressobj(9, _z.DEFLATED, -15)
        comp = c.compress(data[i * bs:(i + 1) * bs]) + c.flush()
        index.append(pos)
        blocks += comp
        pos += len(comp)
    index.append(pos)
    return header + b"".join(struct.pack("<I", x) for x in index) + blocks


def snes_rom(hirom=False, copier=False):
    base = 0xFFC0 if hirom else 0x7FC0
    data = bytearray(0x10000 if hirom else 0x8000)
    data[base:base + 21] = b"SUPER MARIO WORLD    "
    data[base + 0x15] = 0x21 if hirom else 0x20
    struct.pack_into("<HH", data, base + 0x1C, 0x1234 ^ 0xFFFF, 0x1234)
    return (b"\x00" * 512 if copier else b"") + bytes(data)


def vb_rom():
    data = bytearray(0x10000)
    h = len(data) - 0x220
    data[h:h + 7] = b"VB GAME"
    data[h + 25:h + 27] = b"01"
    data[h + 27:h + 31] = b"VWOE"
    return bytes(data)


def chd_v5(tag):
    head = bytearray(124)
    head[0:8] = b"MComprHD"
    struct.pack_into(">II", head, 8, 124, 5)
    struct.pack_into(">Q", head, 48, 124)
    entry = tag + struct.pack(">I", 8)[1:].rjust(4, b"\x00") + struct.pack(">QQ", 0, 0) + b"TRACK:1"
    return bytes(head) + entry


SETTINGS = """<?xml version="1.0"?>
<bool name="CustomEventScripts" value="{scripts}" />
<string name="ROMDirectory" value="" />
<string name="ThemeSet" value="linear-es-de" />
"""


class Env(unittest.TestCase):
    """Fabrique un HOME complet : ~/ES-DE, ~/ROMs, émulateurs factices."""

    custom_event_scripts = "true"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="esdl-test-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.old_env = dict(os.environ)
        os.environ["HOME"] = self.home
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.home, ".config")
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.home, ".cache")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        os.environ["PATH"] = self.bin + ":/usr/bin:/bin"
        os.environ.pop("ESDE_APPDATA_DIR", None)

        self.esde = os.path.join(self.home, "ES-DE")
        self.roms = os.path.join(self.home, "ROMs")
        write(os.path.join(self.esde, "settings", "es_settings.xml"),
              SETTINGS.format(scripts=self.custom_event_scripts))
        self.calls = os.path.join(self.tmp, "calls.log")
        # Émulateurs factices : enregistrent leurs arguments, un par ligne
        rec = '#!/bin/sh\necho "EMU $(basename $0) cwd=$(pwd)" >> "%s"\n' \
              'for a in "$@"; do echo "ARG $a" >> "%s"; done\nexit ${FAKE_RC:-0}\n' % (self.calls, self.calls)
        for name in ("retroarch", "dolphin-emu", "pcsx2-qt", "supermodel"):
            write(os.path.join(self.bin, name), rec, 0o755)
        cores = os.path.join(self.home, ".config", "retroarch", "cores")
        for core in ("mupen64plus_next_libretro.so", "parallel_n64_libretro.so", "mgba_libretro.so",
                     "bsnes_hd_beta_libretro.so", "snes9x_libretro.so", "mame_libretro.so",
                     "mednafen_psx_libretro.so", "dolphin_libretro.so", "genesis_plus_gx_libretro.so"):
            write(os.path.join(cores, core), "")
        # Scripts d'événements
        for ev in ("game-start", "game-end"):
            write(os.path.join(self.esde, "scripts", ev, "lcd.sh"),
                  '#!/bin/sh\necho "%s $1|$2|$3|$4|$ESDE_LAUNCHER" >> "%s"\n' % (ev, self.calls), 0o755)
        write(os.path.join(self.esde, "scripts", "common.conf"), "X=1\n")
        # Gamelists
        write(os.path.join(self.esde, "gamelists", "snes", "gamelist.xml"),
              "<?xml version=\"1.0\"?>\n<alternativeEmulator>\n\t<label>bsnes-hd</label>\n"
              "</alternativeEmulator>\n<gameList>\n</gameList>\n")
        write(os.path.join(self.esde, "gamelists", "gc", "gamelist.xml"),
              "<?xml version=\"1.0\"?>\n<alternativeEmulator>\n\t<label>Dolphin (Standalone)</label>\n"
              "</alternativeEmulator>\n<gameList>\n</gameList>\n")
        write(os.path.join(self.esde, "gamelists", "n64", "gamelist.xml"),
              "<?xml version=\"1.0\"?>\n<gameList>\n"
              "<game><path>./Mario Kart 64.z64</path><name>Mario Kart 64</name></game>\n"
              "<game><path>./Zelda.z64</path><name>Zelda OoT</name><altemulator>ParaLLEl N64</altemulator></game>\n"
              "<game><path>./Other.z64</path><name>Other</name></game>\n"
              "</gameList>\n")
        self.conf_path = os.path.join(self.home, ".config", "es-de-launch.conf")
        self.write_conf()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_conf(self, extra="", ambiguity="fail", scripts="always", priority=""):
        write(self.conf_path,
              "[install]\ntype = manual\nconfig_dir = %s\nresources_dir = %s\n\n"
              "[launch]\nscripts = %s\nambiguity = %s\n\n[systems]\npriority = %s\n\n%s"
              % (self.esde, FIXTURES, scripts, ambiguity, priority, extra))

    def ctx(self):
        conf = L.Conf.load(self.conf_path)
        ns = L.argparse.Namespace(config_dir=None, resources_dir=None, refresh_cache=False)
        return L.build_context(conf, ns)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = L.main(["--conf", self.conf_path] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def rom(self, rel, content=b"\x00" * 64):
        return write(os.path.join(self.roms, rel), content)

    def calls_lines(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls, encoding="utf-8") as fh:
            return [l.rstrip("\n") for l in fh]


# ==========================================================================
# Chargement de la configuration ES-DE
# ==========================================================================

class TestConfigLoading(Env):
    def test_settings_without_root_element(self):
        s = L.load_settings(self.esde)
        self.assertTrue(s["CustomEventScripts"])
        self.assertEqual(s["ROMDirectory"], "")
        self.assertTrue(s["AlternativeEmulatorPerGame"])  # valeur par défaut d'ES-DE
        # getROMDirectory() : <home>/ROMs/ avec barre finale
        self.assertEqual(self.ctx().rom_dir, self.roms + "/")

    def test_real_es_systems(self):
        ctx = self.ctx()
        self.assertEqual(len(ctx.systems), 195)
        n64 = ctx.systems["n64"]
        self.assertEqual(n64.path, os.path.join(self.roms, "n64"))
        self.assertIn(".z64", n64.extensions)
        self.assertNotIn(".64", n64.extensions)
        self.assertEqual(n64.commands[0][0], "Mupen64Plus-Next")
        self.assertEqual(ctx.systems["doom"].platforms, ["pc", "pcwindows"])
        # coquille réelle : ".cue CUE"
        self.assertIn("CUE", ctx.systems["pcfx"].extensions)

    def test_custom_system_overrides_bundled(self):
        write(os.path.join(self.esde, "custom_systems", "es_systems.xml"),
              "<systemList><system><name>n64</name><fullname>Mon N64</fullname>"
              "<path>%ROMPATH%/n64</path><extension>.z64 .64</extension>"
              "<command label=\"Perso\">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/parallel_n64_libretro.so %ROM%</command>"
              "<platform>n64</platform><theme>n64</theme></system></systemList>")
        ctx = self.ctx()
        self.assertEqual(ctx.systems["n64"].fullname, "Mon N64")
        self.assertEqual(ctx.systems["n64"].extensions, [".z64", ".64"])
        self.assertTrue(ctx.systems["n64"].custom)
        self.assertEqual(len(ctx.systems), 195)

    def test_custom_new_system(self):
        write(os.path.join(self.esde, "custom_systems", "es_systems.xml"),
              "<systemList><system><name>maconsole</name><fullname>Ma console</fullname>"
              "<path>%ROMPATH%/maconsole</path><extension>.mc</extension>"
              "<command label=\"RA\">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%</command>"
              "<platform>maconsole</platform><theme>maconsole</theme></system></systemList>")
        ctx = self.ctx()
        self.assertEqual(len(ctx.systems), 196)
        rom = self.rom("maconsole/jeu.mc")
        self.assertEqual(L.detect_system(ctx, rom).system, "maconsole")
        self.assertIn("mgba_libretro.so", L.build_command(ctx, ctx.systems["maconsole"], rom).command)

    def test_custom_find_rules_override(self):
        perso = write(os.path.join(self.home, "emus", "mon-retroarch"), "#!/bin/sh\n", 0o755)
        cores = os.path.join(self.home, "mes-coeurs")
        write(os.path.join(cores, "mupen64plus_next_libretro.so"), "")
        write(os.path.join(self.esde, "custom_systems", "es_find_rules.xml"),
              "<ruleList><emulator name=\"RETROARCH\"><rule type=\"staticpath\">"
              "<entry>~/emus/mon-retroarch</entry></rule></emulator>"
              "<core name=\"RETROARCH\"><rule type=\"corepath\"><entry>~/mes-coeurs</entry></rule></core>"
              "<emulator name=\"NOUVEL-EMU\"><rule type=\"systempath\"><entry>nouvel-emu</entry></rule></emulator>"
              "</ruleList>")
        ctx = self.ctx()
        self.assertIn("NOUVEL-EMU", ctx.emulators)
        self.assertIn("MAME", ctx.emulators)  # règles embarquées conservées
        rom = self.rom("n64/Other.z64", n64_rom())
        argv = shlex.split(L.build_command(ctx, ctx.systems["n64"], rom).command)
        self.assertEqual(argv[:3], [perso, "-L", os.path.join(cores, "mupen64plus_next_libretro.so")])

    def test_custom_rom_directory(self):
        other = os.path.join(self.home, "Jeux", "ROMs")
        write(os.path.join(self.esde, "settings", "es_settings.xml"),
              '<string name="ROMDirectory" value="~/Jeux/ROMs" />\n')
        ctx = self.ctx()
        self.assertEqual(ctx.systems["snes"].path, os.path.join(other, "snes"))
        rom = write(os.path.join(other, "snes", "jeu.sfc"), b"\x00")
        self.assertEqual(L.detect_system(ctx, rom).method, "chemin")

    def test_malformed_custom_xml(self):
        write(os.path.join(self.esde, "custom_systems", "es_systems.xml"), "<systemList><system>")
        with self.assertRaises(L.LaunchError) as cm:
            self.ctx()
        self.assertEqual(cm.exception.code, L.EXIT_CONFIG)
        self.assertIn("XML invalide", str(cm.exception))

    def test_find_rules(self):
        ctx = self.ctx()
        self.assertIn("RETROARCH", ctx.emulators)
        self.assertIn("PLAY!", ctx.emulators)
        self.assertIn("~/.config/retroarch/cores", ctx.cores["RETROARCH"]["corepath"])
        self.assertIn("retroarch", ctx.emulators["RETROARCH"]["systempath"])

    def test_gamelist(self):
        ctx = self.ctx()
        g = L.Gamelist.load(ctx, ctx.systems["snes"])
        self.assertEqual(g.alt_emulator, "bsnes-hd")
        g = L.Gamelist.load(ctx, ctx.systems["n64"])
        game = g.game(os.path.join(self.roms, "n64", "Zelda.z64"))
        self.assertEqual(game["altemulator"], "ParaLLEl N64")


# ==========================================================================
# Détection du système
# ==========================================================================

class TestDetection(Env):
    def test_in_library(self):
        rom = self.rom("n64/Mario Kart 64.z64", n64_rom())
        det = L.detect_system(self.ctx(), rom)
        self.assertEqual((det.system, det.level, det.method), ("n64", L.CERTAIN, "chemin"))
        self.assertTrue(det.in_library)

    def test_in_library_subfolder(self):
        rom = self.rom("psx/Final Fantasy VII/disc1.cue", 'FILE "disc1.bin" BINARY\n')
        det = L.detect_system(self.ctx(), rom)
        self.assertEqual(det.system, "psx")

    def test_undeclared_extension_in_system_dir(self):
        # Cas de l'énoncé : ~/ROMs/n64/Mario Kart.64
        rom = self.rom("n64/Mario Kart.64", n64_rom())
        ctx = self.ctx()
        det = L.detect_system(ctx, rom)
        self.assertEqual(det.system, "n64")
        self.assertFalse(det.in_library)
        self.assertTrue(any("ne l'afficherait pas" in w for w in det.warnings))
        self.assertEqual(L.suggested_extension(ctx, ctx.systems["n64"], rom), ".z64")

    def test_case_mismatch_warning(self):
        rom = self.rom("ps2/game.nrg", b"\x00" * 64)  # ps2 déclare ".ngr .NRG"
        det = L.detect_system(self.ctx(), rom)
        self.assertEqual(det.system, "ps2")
        self.assertTrue(any("casse" in w for w in det.warnings))

    def test_outside_library_header_and_priority(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.z64"), n64_rom())
        # n64 et n64dd acceptent .z64 et partagent la plateforme n64
        with self.assertRaises(L.LaunchError) as cm:
            L.detect_system(self.ctx(), rom)
        self.assertEqual(cm.exception.code, L.EXIT_SYSTEM)
        self.write_conf(priority="n64, snes")
        det = L.detect_system(self.ctx(), rom)
        self.assertEqual(det.system, "n64")
        self.assertIn("priorité [systems]", det.method)
        self.assertEqual(det.level, L.PROBABLE)

    def test_extension_priority_wins_over_global(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.z64"), n64_rom())
        self.write_conf(priority="n64", extra="[extensions]\nz64 = n64dd, n64\n")
        det = L.detect_system(self.ctx(), rom)
        self.assertEqual(det.system, "n64dd")

    def test_ambiguity_first(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.z64"), n64_rom())
        self.write_conf(ambiguity="first")
        self.assertEqual(L.detect_system(self.ctx(), rom).system, "n64")

    def test_exclude(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.z64"), n64_rom())
        self.write_conf(extra="", ambiguity="fail")
        with open(self.conf_path, "a") as fh:
            fh.write("")
        conf = L.Conf.load(self.conf_path)
        conf.cp.set("systems", "exclude", "n64dd")
        ctx = self.ctx()
        ctx.conf = conf
        self.assertEqual(L.detect_system(ctx, rom).system, "n64")

    def test_gba_header(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.bin"), gba_rom())
        det = L.detect_system(self.ctx(), rom)
        self.assertEqual(det.system, "gba")
        self.assertIn("en-tête GBA", det.method)

    def test_ps2_iso_system_cnf(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.iso"),
                    iso_with_system_cnf("BOOT2 = cdrom0:\\SLES_123.45;1\r\nVER = 1.00\r\n"))
        det = L.detect_system(self.ctx(), rom)
        self.assertEqual((det.system, det.level), ("ps2", L.CERTAIN))

    def test_psx_iso_system_cnf(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.iso"),
                    iso_with_system_cnf("BOOT = cdrom:\\SCES_123.45;1\r\n"))
        self.assertEqual(L.detect_system(self.ctx(), rom).system, "psx")

    def test_gamecube_disc(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.iso"), gc_disc())
        det = L.detect_system(self.ctx(), rom)
        self.assertEqual((det.system, det.level), ("gc", L.CERTAIN))

    def test_zip_content(self):
        p = os.path.join(self.home, "Downloads", "jeu.zip")
        os.makedirs(os.path.dirname(p))
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("Jeu (Europe).gba", gba_rom())
        self.assertEqual(L.detect_system(self.ctx(), p).system, "gba")

    def test_folders_section(self):
        d = os.path.join(self.home, "Telechargements", "snes")
        rom = write(os.path.join(d, "jeu.zip"), b"PK\x05\x06" + b"\x00" * 18)
        self.write_conf(extra="[folders]\n%s = snes\n" % d)
        self.assertEqual(L.detect_system(self.ctx(), rom).system, "snes")

    def test_forced_system(self):
        rom = write(os.path.join(self.home, "x.bin"), b"\x00")
        self.assertEqual(L.detect_system(self.ctx(), rom, forced="psx").system, "psx")
        with self.assertRaises(L.LaunchError):
            L.detect_system(self.ctx(), rom, forced="nope")

    def test_unknown(self):
        rom = write(os.path.join(self.home, "x.qqq"), b"\x00")
        with self.assertRaises(L.LaunchError) as cm:
            L.detect_system(self.ctx(), rom)
        self.assertEqual(cm.exception.code, L.EXIT_SYSTEM)


# ==========================================================================
# Construction de la commande
# ==========================================================================

class TestCommand(Env):
    def cmd(self, system, rom, label=None):
        ctx = self.ctx()
        return L.build_command(ctx, ctx.systems[system], rom, label)

    def test_default_retroarch(self):
        rom = self.rom("n64/Mario Kart 64.z64", n64_rom())
        res = self.cmd("n64", rom)
        self.assertEqual(res.label, "Mupen64Plus-Next")
        argv = shlex.split(res.command)
        self.assertEqual(argv, [os.path.join(self.bin, "retroarch"), "-L",
                                os.path.join(self.home, ".config/retroarch/cores/mupen64plus_next_libretro.so"),
                                rom])

    def test_game_altemulator(self):
        rom = self.rom("n64/Zelda.z64", n64_rom())
        res = self.cmd("n64", rom)
        self.assertEqual(res.label, "ParaLLEl N64")
        self.assertIn("parallel_n64_libretro.so", res.command)

    def test_system_alternative_emulator(self):
        rom = self.rom("snes/Mario World.sfc")
        res = self.cmd("snes", rom)
        self.assertEqual(res.label, "bsnes-hd")
        self.assertIn("bsnes_hd_beta_libretro.so", res.command)

    def test_forced_label_and_unknown_label(self):
        rom = self.rom("snes/Mario World.sfc")
        self.assertEqual(self.cmd("snes", rom, "Snes9x - Current").label, "Snes9x - Current")
        with self.assertRaises(L.LaunchError) as cm:
            self.cmd("snes", rom, "Inexistant")
        self.assertEqual(cm.exception.code, L.EXIT_EMULATOR)

    def test_missing_emulator(self):
        rom = self.rom("switch/jeu.nsp")
        with self.assertRaises(L.LaunchError) as cm:
            self.cmd("switch", rom)
        self.assertEqual(cm.exception.code, L.EXIT_EMULATOR)
        self.assertIn("systempath:eden", str(cm.exception))

    def test_missing_core(self):
        rom = self.rom("nds/jeu.nds")
        with self.assertRaises(L.LaunchError) as cm:
            self.cmd("nds", rom)
        self.assertIn("melondsds_libretro.so", str(cm.exception))

    def test_inject_and_standalone(self):
        rom = self.rom("gc/Mario Sunshine.rvz")
        res = self.cmd("gc", rom)  # alternativeEmulator = Dolphin (Standalone)
        self.assertEqual(shlex.split(res.command), [os.path.join(self.bin, "dolphin-emu"), "-b", "-e", rom])
        write(os.path.join(self.roms, "gc", "Mario Sunshine.esprefix"), "GDK_BACKEND=x11\n")
        res = self.cmd("gc", rom)
        self.assertTrue(res.command.startswith("GDK_BACKEND=x11 "))

    def test_startdir(self):
        rom = self.rom("model3/scud.zip")
        res = self.cmd("model3", rom, "Supermodel (Standalone)")
        self.assertEqual(res.cwd, os.path.join(self.roms, "model3"))
        self.assertNotIn("STARTDIR", res.command)
        self.assertIn("-log-output=%s/Config/Supermodel.log" % os.path.join(self.roms, "model3"), res.command)

    def test_startdir_tilde(self):
        write(os.path.join(self.bin, "mame"), "#!/bin/sh\n", 0o755)
        rom = self.rom("arcade/pacman.zip")
        res = self.cmd("arcade", rom, "MAME (Standalone)")
        self.assertEqual(res.cwd, os.path.join(self.home, ".mame"))
        self.assertIn("-rompath %s\\;%s//arcade pacman" % (os.path.join(self.roms, "arcade"), self.roms),
                      res.command)
        self.assertEqual(res.shell_command, "cd %s && %s" % (os.path.join(self.home, ".mame"), res.command))

    def test_mame_libretro_quoting(self):
        rom = self.rom("apple2/Karateka (1984) l'original.dsk")
        res = self.cmd("apple2", rom, "MAME - Current")
        argv = shlex.split(res.command)
        gd = os.path.join(self.roms, "apple2")
        # %ROMPATH% garde son / final (ROMs//apple2) et %FILENAME% n'est jamais échappé
        self.assertEqual(argv[3], 'apple2e -rompath "%s;%s//apple2" -gameio joy -flop1 "%s/%s"'
                         % (gd, self.roms, gd, os.path.basename(rom)))

    def test_special_characters_in_rom(self):
        rom = self.rom("psx/Jeu $HOME `id` & (v1) [!].cue", 'FILE "x.bin" BINARY\n')
        res = self.cmd("psx", rom)
        self.assertEqual(shlex.split(res.command)[-1], rom)

    def test_desktop_shortcut(self):
        rom = self.rom("ps3/Demon's Souls.desktop",
                       "[Desktop Entry]\nType=Application\nName=DS\nExec=/usr/bin/rpcs3 --no-gui %U \"/games/BLUS30443\"\n")
        res = self.cmd("ps3", rom, "RPCS3 Shortcut (Standalone)")
        self.assertEqual(res.command, '/usr/bin/rpcs3 --no-gui  "/games/BLUS30443"')
        self.assertIsNone(res.cwd)  # pas de Path= ni de %STARTDIR% : dossier courant
        # Le 1er argument des scripts devient la ligne Exec= (comportement d'ES-DE)
        self.assertEqual(res.rom_path, '/usr/bin/rpcs3 --no-gui  "/games/BLUS30443"')

    def test_shell_script_shortcut(self):
        rom = self.rom("n64/port.sh", "#!/bin/sh\n")
        res = self.cmd("n64", rom, "Shortcut or script")
        self.assertEqual(shlex.split(res.command), [shutil.which("bash"), rom])

    def test_placeholder(self):
        rom = self.rom("xboxone/x.zip")
        with self.assertRaises(L.LaunchError) as cm:
            self.cmd("xboxone", rom)
        self.assertIn("PLACEHOLDER", str(cm.exception))

    def test_rpcs3_gameid_literal(self):
        write(os.path.join(self.bin, "rpcs3"), "#!/bin/sh\n", 0o755)
        rom = self.rom("ps3/Demon.ps3")
        write(os.path.join(self.roms, "ps3", "Demon.ps3"), "BLUS30443\n")
        res = self.cmd("ps3", rom, "RPCS3 Game Serial (Standalone)")
        self.assertTrue(res.command.endswith("--no-gui %RPCS3_GAMEID%:BLUS30443"))

    def test_staticpath_glob_and_pipe(self):
        ctx = self.ctx()
        apps = os.path.join(self.home, "Applications")
        write(os.path.join(apps, "Foo-1.2.AppImage"), "#!/bin/sh\n", 0o755)
        # Les règles staticpath viennent avant systempath dans le fichier : ES-DE essaie
        # quand même tous les systempath d'abord.
        ctx.emulators["FOO"] = {"systempath": ["introuvable-xyz"],
                                "staticpath": ["~/Applications/Foo*.AppImage|flatpak run --command=foo org.Foo"]}
        cmd, exe, status, entry, tried = L.find_emulator(ctx, "%EMULATOR_FOO% -x %ROM%")
        self.assertEqual(status, L.FOUND)
        self.assertEqual(exe, "flatpak run --command=foo org.Foo")
        self.assertEqual(cmd, "flatpak run --command=foo org.Foo -x %ROM%")
        self.assertEqual(tried[0], "systempath:introuvable-xyz")
        ctx.emulators["FOO"]["staticpath"] = ["~/Applications/Foo*.AppImage"]
        cmd, exe, status, entry, tried = L.find_emulator(ctx, "%EMULATOR_FOO%")
        self.assertEqual(exe, os.path.join(apps, "Foo-1.2.AppImage"))

    def test_systempath_before_staticpath_and_no_exec_bit(self):
        ctx = self.ctx()
        write(os.path.join(self.bin, "foo-emu"), "pas exécutable\n")  # ES-DE ne teste pas le bit x
        write(os.path.join(self.home, "foo"), "#!/bin/sh\n", 0o755)
        ctx.emulators["FOO"] = {"systempath": ["foo-emu"], "staticpath": ["~/foo"]}
        _cmd, exe, status, _e, _t = L.find_emulator(ctx, "%EMULATOR_FOO%")
        self.assertEqual(exe, os.path.join(self.bin, "foo-emu"))

    def test_no_rules_and_method_two(self):
        ctx = self.ctx()
        ctx.emulators["VIDE"] = {"systempath": [], "staticpath": []}
        self.assertEqual(L.find_emulator(ctx, "%EMULATOR_VIDE% %ROM%")[2], L.NO_RULES)
        cmd, exe, status, entry, _t = L.find_emulator(ctx, "retroarch -L x %ROM%")
        self.assertEqual((exe, status), (os.path.join(self.bin, "retroarch"), L.FOUND))
        self.assertEqual(L.find_emulator(ctx, "inexistant-xyz %ROM%")[2], L.NOT_FOUND)

    def test_escaping_helpers(self):
        # getEscapedPath() : \\ ' " ! $ ^ & * ( ) { } [ ] ? ; < > et l'espace ; pas ` | # ~
        self.assertEqual(L.es_escape("/a b/l'x (1) [!].zip"), "/a\\ b/l\\'x\\ \\(1\\)\\ \\[\\!\\].zip")
        self.assertEqual(L.es_escape("a`b|c#~d"), "a`b|c#~d")
        self.assertEqual(L.es_escape("a\\b"), "a\\\\b")
        self.assertEqual(L.es_escape("a\\ b"), "a\\\\ b")  # espace déjà précédé d'un \\
        self.assertEqual(L.es_extension("/x/jeu"), ".")
        self.assertEqual(L.es_extension("/x/jeu.tar.gz"), ".gz")
        self.assertEqual(L.strip_field_codes('app %U --x "%%f" %f'), 'app  --x "%f"')


# ==========================================================================
# Fidélité au code d'ES-DE 3.5.0 (FileData.cpp, SystemData.cpp, Scripting.cpp…)
# ==========================================================================

class TestEsdeFidelity(Env):
    def cmd(self, system, rom, label=None, ctx=None):
        ctx = ctx or self.ctx()
        return L.build_command(ctx, ctx.systems[system], rom, label)

    def custom_system(self, command, extensions=".x", name="essai", extra=""):
        write(os.path.join(self.esde, "custom_systems", "es_systems.xml"),
              "%s<systemList><system><name>%s</name><fullname>Essai</fullname>"
              "<path>%%ROMPATH%%/%s</path><extension>%s</extension>"
              "<command label=\"A\">%s</command><command label=\"B\">retroarch %%ROM%%</command>"
              "<platform>essai</platform><theme>essai</theme></system></systemList>"
              % (extra, name, name, extensions, command.replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")))

    def test_game_altemulator_invalid_falls_to_default_not_system(self):
        write(os.path.join(self.esde, "gamelists", "snes", "gamelist.xml"),
              "<alternativeEmulator><label>bsnes-hd</label></alternativeEmulator><gameList>"
              "<game><path>./jeu.sfc</path><name>Jeu</name><altemulator>Disparu</altemulator></game>"
              "</gameList>")
        rom = self.rom("snes/jeu.sfc")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            res = self.cmd("snes", rom)
        self.assertEqual(res.label, "Snes9x - Current")  # pas bsnes-hd
        self.assertIn("Disparu", err.getvalue())

    def test_alternative_emulator_per_game_setting(self):
        write(os.path.join(self.esde, "settings", "es_settings.xml"),
              '<bool name="AlternativeEmulatorPerGame" value="false" />\n')
        rom = self.rom("n64/Zelda.z64", n64_rom())
        self.assertEqual(self.cmd("n64", rom).label, "Mupen64Plus-Next")

    def test_invalid_system_alternative(self):
        write(os.path.join(self.esde, "gamelists", "snes", "gamelist.xml"),
              "<gameList><alternativeEmulator><label>Inconnu</label></alternativeEmulator></gameList>")
        rom = self.rom("snes/jeu.sfc")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.cmd("snes", rom).label, "Snes9x - Current")

    def test_inject_concatenates_lines_and_quoted_form(self):
        self.custom_system('%INJECT%="%BASENAME%.args" %EMULATOR_RETROARCH% %INJECT%=%BASENAME%.more %ROM%')
        rom = self.rom("essai/jeu.x")
        write(os.path.join(self.roms, "essai", "jeu.args"), "VAR=1\r\n")
        write(os.path.join(self.roms, "essai", "jeu.more"), "--a\n--b\n")
        res = self.cmd("essai", rom)
        self.assertEqual(res.command, "VAR=1 %s --a--b %s" % (os.path.join(self.bin, "retroarch"), L.es_escape(rom)))

    def test_inject_too_big_is_ignored(self):
        self.custom_system("retroarch %INJECT%=%BASENAME%.args %ROM%")
        rom = self.rom("essai/jeu.x")
        write(os.path.join(self.roms, "essai", "jeu.args"), "x" * 5000)
        with contextlib.redirect_stderr(io.StringIO()):
            res = self.cmd("essai", rom)
        self.assertNotIn("xxxx", res.command)

    def test_quoted_startdir_with_spaces_is_created_at_launch(self):
        self.custom_system('%STARTDIR%="~/Mon dossier" %EMULATOR_RETROARCH% %ROM%')
        rom = self.rom("essai/jeu.x")
        res = self.cmd("essai", rom)
        self.assertEqual(res.cwd, os.path.join(self.home, "Mon dossier"))
        self.assertEqual(res.shell_command, "cd %s && %s" % (L.es_escape(res.cwd), res.command))
        code, _o, err = self.run_main("--no-scripts", rom)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(res.cwd))

    def test_quoted_core(self):
        self.custom_system('retroarch -L "%CORE_RETROARCH%/mgba_libretro.so" %ROM%')
        rom = self.rom("essai/jeu.x")
        res = self.cmd("essai", rom)
        # Sans %EMULATOR_, ES-DE vérifie le 1er mot mais ne le remplace pas
        self.assertEqual(res.command, "retroarch -L %s %s" % (
                         os.path.join(self.home, ".config/retroarch/cores/mgba_libretro.so"), L.es_escape(rom)))

    def test_emupath(self):
        write(os.path.join(self.bin, "cores", "x_libretro.so"), "")
        self.custom_system("retroarch -L %EMUPATH%/cores/x_libretro.so %ROM%")
        rom = self.rom("essai/jeu.x")
        self.assertIn(os.path.join(self.bin, "cores/x_libretro.so"), self.cmd("essai", rom).command)

    def test_raw_variables_and_tilde(self):
        self.custom_system("retroarch %ROMRAW% %ROMRAWWIN% %BASENAME% %FILENAME% %GAMEDIRRAW% ~/x")
        rom = self.rom("essai/un jeu (v1).x")
        res = self.cmd("essai", rom)
        d = os.path.join(self.roms, "essai")
        self.assertEqual(res.command, "retroarch %s %s un jeu (v1) un jeu (v1).x %s %s/x" % (
            rom, rom.replace("/", "\\"), d, self.home))

    def test_directory_interpreted_as_file(self):
        self.custom_system("retroarch %ROM% %BASENAME% %FILENAME%", extensions=".ps3")
        d = os.path.join(self.roms, "essai", "Jeu.ps3")
        inner = write(os.path.join(d, "Jeu.ps3"), "x")
        det = L.detect_system(self.ctx(), d)
        self.assertEqual((det.system, det.method), ("essai", "chemin"))
        res = self.cmd("essai", d)
        self.assertEqual(res.command, "retroarch %s Jeu Jeu.ps3" % inner)

    def test_no_extension_means_dot(self):
        self.custom_system("retroarch %ROM% %FILENAME%", extensions=". .x")
        rom = self.rom("essai/default")
        self.assertEqual(L.detect_system(self.ctx(), rom).system, "essai")
        self.assertTrue(self.cmd("essai", rom).command.endswith(" default."))

    def test_load_exclusive(self):
        self.custom_system("retroarch %ROM%", extra="<loadExclusive/>")
        ctx = self.ctx()
        self.assertEqual(list(ctx.systems), ["essai"])

    def test_duplicate_labels_and_label_rules(self):
        write(os.path.join(self.esde, "custom_systems", "es_systems.xml"),
              "<systemList><system><name>essai</name><fullname>E</fullname><path>%ROMPATH%/essai</path>"
              "<extension>.x,.y</extension><command label=\"A\">a %ROM%</command>"
              "<command label=\"A\">b %ROM%</command><command>c %ROM%</command>"
              "<command label=\"D\">d %ROM%</command><platform>Essai, Autre</platform></system></systemList>")
        with contextlib.redirect_stderr(io.StringIO()):
            s = self.ctx().systems["essai"]
        self.assertEqual(s.commands, [("A", "a %ROM%")])
        self.assertEqual(s.extensions, [".x", ".y"])
        self.assertEqual(s.platforms, ["essai", "autre"])
        self.assertEqual(s.theme, "essai")  # thème absent : nom du système

    def test_two_systems_share_a_folder(self):
        # Cas réel : n64vc (personnalisé, .wad -> Dolphin) lit le même dossier que n64
        write(os.path.join(self.esde, "custom_systems", "es_systems.xml"),
              "<systemList><system><name>n64vc</name><fullname>Nintendo 64 Virtual Console</fullname>"
              "<path>%ROMPATH%/n64</path><extension>.wad .WAD</extension>"
              "<command label=\"Dolphin (Standalone)\">%INJECT%=%BASENAME%.esprefix %EMULATOR_DOLPHIN% -b -e %ROM%"
              "</command><platform>n64</platform><theme>n64</theme></system></systemList>")
        ctx = self.ctx()
        wad = self.rom("n64/Mario Kart 64.wad")
        z64 = self.rom("n64/Mario Kart 64.z64", n64_rom())
        det = L.detect_system(ctx, wad)
        self.assertEqual((det.system, det.method, det.warnings), ("n64vc", "chemin", []))
        res = L.build_command(ctx, ctx.systems["n64vc"], wad)
        self.assertEqual(res.command, "%s -b -e %s" % (os.path.join(self.bin, "dolphin-emu"), L.es_escape(wad)))
        det = L.detect_system(ctx, z64)
        self.assertEqual((det.system, det.warnings), ("n64", []))
        self.assertIn("mupen64plus_next", L.build_command(ctx, ctx.systems["n64"], z64).command)

    def test_resources_override_in_data_dir(self):
        d = os.path.join(self.esde, "resources", "systems", L.SYSTEMS_DIR)
        os.makedirs(d)
        with open(os.path.join(FIXTURES, "es_find_rules.xml")) as fh:
            content = fh.read().replace("<entry>retroarch</entry>", "<entry>retroarch-perso</entry>")
        write(os.path.join(d, "es_find_rules.xml"), content)
        ctx = self.ctx()
        self.assertEqual(ctx.find_rules_file, os.path.join(d, "es_find_rules.xml"))
        self.assertEqual(ctx.systems_file, os.path.join(FIXTURES, "es_systems.xml"))
        self.assertIn("retroarch-perso", ctx.emulators["RETROARCH"]["systempath"])

    def test_malformed_custom_find_rules_is_skipped(self):
        write(os.path.join(self.esde, "custom_systems", "es_find_rules.xml"), "<ruleList><emulator>")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ctx = self.ctx()
        self.assertIn("RETROARCH", ctx.emulators)

    def test_legacy_gamelist_location(self):
        write(os.path.join(self.esde, "settings", "es_settings.xml"),
              '<bool name="LegacyGamelistFileLocation" value="true" />\n')
        write(os.path.join(self.roms, "snes", "gamelist.xml"),
              "<gameList><alternativeEmulator><label>bsnes</label></alternativeEmulator></gameList>")
        rom = self.rom("snes/jeu.sfc")
        ctx = self.ctx()
        self.assertEqual(L.choose_command(ctx, ctx.systems["snes"], rom)[0], "bsnes")

    def test_desktop_path_ignored_with_startdir_and_last_exec_wins(self):
        self.custom_system("%STARTDIR%=%GAMEDIR% %ENABLESHORTCUTS% %EMULATOR_OS-SHELL% %ROM%", extensions=".desktop")
        rom = self.rom("essai/jeu.desktop", "#!/usr/bin/env xdg-open\n  [Desktop Entry]\n"
                       "Exec=premier\nPath=/tmp\n[Desktop Action x]\nExec=dernier %%u %u\n")
        res = self.cmd("essai", rom)
        self.assertEqual(res.command, "dernier %u")
        self.assertEqual(res.cwd, os.path.join(self.roms, "essai"))

    def test_run_in_background_flag_and_setting(self):
        rom = self.rom("steam/jeu.desktop", "[Desktop Entry]\nExec=steam steam://rungameid/1\n")
        res = self.cmd("steam", rom)
        self.assertTrue(res.run_in_background)
        self.assertEqual(res.command, "steam steam://rungameid/1")
        write(os.path.join(self.esde, "settings", "es_settings.xml"), '<bool name="RunInBackground" value="1"/>\n')
        rom2 = self.rom("n64/Other.z64", n64_rom())
        self.assertTrue(self.cmd("n64", rom2).run_in_background)

    def test_placeholder_command(self):
        rom = self.rom("xboxone/x.zip")
        with self.assertRaises(L.LaunchError) as cm:
            self.cmd("xboxone", rom)
        self.assertIn("PLACEHOLDER", str(cm.exception))

    def test_esde_appdata_dir_and_portable_txt(self):
        os.environ["ESDE_APPDATA_DIR"] = "~/autre-ES-DE"
        self.assertEqual(L.detect_config_dir(self.home), os.path.join(self.home, "autre-ES-DE"))
        del os.environ["ESDE_APPDATA_DIR"]
        app = os.path.join(self.tmp, "esde-portable")
        write(os.path.join(app, "es-de"), b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64, 0o755)
        write(os.path.join(app, "portable.txt"), "data\n")
        os.makedirs(os.path.join(app, "data"))
        self.assertEqual(L.esde_home(os.path.join(app, "es-de")), app + "/data")

    def test_scripts_command_line_and_non_executable(self):
        write(os.path.join(self.esde, "scripts", "game-start", "a-non-exec.sh"), "echo x\n")
        line = L.script_command_line("/s/x.sh", ["/r/a\\ b.z64", "Nom", "n64", '"déjà"'])
        self.assertEqual(line, '"/s/x.sh" "/r/a\\ b.z64" "Nom" "n64" "déjà"')
        ctx = self.ctx()
        self.assertEqual([os.path.basename(s) for s in L.event_scripts(ctx, "game-start")],
                         ["a-non-exec.sh", "lcd.sh"])  # ES-DE ne filtre pas le bit x


# ==========================================================================
# Lancement et scripts
# ==========================================================================

class TestLaunch(Env):
    def test_launch_runs_scripts_in_order(self):
        rom = self.rom("n64/Mario Kart 64.z64", n64_rom())
        code, _out, err = self.run_main(rom)
        self.assertEqual(code, 0, err)
        lines = self.calls_lines()
        # ES-DE passe aux scripts le chemin ÉCHAPPÉ entre guillemets : les \\ restent
        esc = L.es_escape(rom)
        self.assertTrue(lines[0].startswith("game-start %s|Mario Kart 64|n64|Nintendo 64|es-de-launch" % esc))
        self.assertTrue(lines[1].startswith("EMU retroarch"))
        self.assertIn("ARG %s" % rom, lines)
        self.assertTrue(lines[-1].startswith("game-end %s|Mario Kart 64|n64" % esc))

    def test_exit_code_and_game_end_on_failure(self):
        rom = self.rom("n64/Other.z64", n64_rom())
        os.environ["FAKE_RC"] = "3"
        code, _out, _err = self.run_main(rom)
        self.assertEqual(code, 3)
        self.assertTrue(self.calls_lines()[-1].startswith("game-end"))

    def test_no_scripts_option(self):
        rom = self.rom("n64/Other.z64", n64_rom())
        self.run_main("--no-scripts", rom)
        self.assertFalse(any(l.startswith("game-") for l in self.calls_lines()))

    def test_scripts_auto_follows_setting(self):
        write(os.path.join(self.esde, "settings", "es_settings.xml"), SETTINGS.format(scripts="false"))
        self.write_conf(scripts="auto")
        rom = self.rom("n64/Other.z64", n64_rom())
        self.run_main(rom)
        self.assertFalse(any(l.startswith("game-") for l in self.calls_lines()))
        self.run_main("--scripts", rom)
        self.assertTrue(any(l.startswith("game-start") for l in self.calls_lines()))

    def test_error_before_launch_runs_no_script(self):
        rom = self.rom("switch/jeu.nsp")
        code, _o, err = self.run_main(rom)
        self.assertEqual(code, L.EXIT_EMULATOR)
        self.assertEqual(self.calls_lines(), [])

    def test_startdir_is_used(self):
        rom = self.rom("model3/scud.zip")
        code, _o, err = self.run_main("--emulator", "Supermodel (Standalone)", rom)
        self.assertEqual(code, 0, err)
        self.assertIn("EMU supermodel cwd=%s" % os.path.join(self.roms, "model3"), self.calls_lines())

    def test_tilde_in_argument(self):
        self.rom("n64/Other.z64", n64_rom())
        code, out, err = self.run_main("--output-cmd", "~/ROMs/n64/Other.z64")
        self.assertEqual(code, 0, err)
        self.assertIn(os.path.join(self.roms, "n64", "Other.z64"), out)

    def test_output_cmd_json(self):
        rom = self.rom("gc/Mario Sunshine.rvz")
        code, out, _err = self.run_main("--output-cmd", "--json", rom)
        data = json.loads(out)
        self.assertEqual(data["system"], "gc")
        self.assertEqual(data["emulator_label"], "Dolphin (Standalone)")
        self.assertEqual(len(data["scripts"]["start"]), 1)
        self.assertEqual(self.calls_lines(), [])  # rien n'est exécuté

    def test_two_launches_at_once(self):
        # ES-DE n'a pas de verrou : un 2e lancement pendant le 1er doit fonctionner
        import subprocess
        write(os.path.join(self.bin, "retroarch"),
              '#!/bin/sh\necho "EMU start" >> "%s"\nsleep 2\n' % self.calls, 0o755)
        rom = self.rom("n64/Other.z64", n64_rom())
        first = subprocess.Popen([sys.executable, SCRIPT, "--conf", self.conf_path, "--no-scripts", rom],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            if "EMU start" in self.calls_lines():
                break
            time.sleep(0.05)
        write(os.path.join(self.bin, "retroarch"), "#!/bin/sh\nexit 0\n", 0o755)
        code, _o, err = self.run_main("--no-scripts", rom)
        first.communicate(timeout=10)
        first.stderr.close() if first.stderr else None
        self.assertEqual(code, 0, err)
        self.assertEqual(first.returncode, 0)

    def test_sigterm_forwarded_and_game_end_runs(self):
        import signal
        import subprocess
        write(os.path.join(self.bin, "retroarch"),
              '#!/bin/sh\necho "EMU start" >> "%s"\ntrap \'echo "EMU term" >> "%s"; exit 143\' TERM\n'
              'while true; do sleep 0.05; done\n' % (self.calls, self.calls), 0o755)
        rom = self.rom("n64/Other.z64", n64_rom())
        p = subprocess.Popen([sys.executable, SCRIPT, "--conf", self.conf_path, rom],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            if "EMU start" in self.calls_lines():
                break
            time.sleep(0.05)
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=10)
        lines = self.calls_lines()
        self.assertIn("EMU term", lines)
        self.assertTrue(lines[-1].startswith("game-end"))
        self.assertEqual(p.returncode, 143)

    def test_missing_file(self):
        code, _o, _e = self.run_main(os.path.join(self.home, "absent.z64"))
        self.assertEqual(code, L.EXIT_USAGE)


# ==========================================================================
# --info et --copy
# ==========================================================================

class TestInfoCopy(Env):
    def test_info_json(self):
        rom = write(os.path.join(self.home, "Downloads", "Mario Kart.64"), n64_rom())
        self.write_conf(priority="n64")
        code, out, err = self.run_main("--info", "--json", rom)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["system"]["name"], "n64")
        self.assertEqual(data["library"]["suggested_extension"], ".z64")
        self.assertEqual(data["library"]["proposed_path"], os.path.join(self.roms, "n64", "Mario Kart.z64"))
        self.assertEqual(data["emulator"]["label"], "Mupen64Plus-Next")
        self.assertEqual(len(data["file"]["hashes"]["sha1"]), 40)

    def test_info_text(self):
        rom = self.rom("n64/Mario Kart 64.z64", n64_rom())
        code, out, _ = self.run_main("--info", rom)
        self.assertEqual(code, 0)
        self.assertIn("Système       : n64 (Nintendo 64)", out)
        self.assertIn("Gamelist      : name=Mario Kart 64", out)

    def test_copy_cue_with_bins(self):
        d = os.path.join(self.home, "Downloads", "FF7")
        write(os.path.join(d, "ff7.bin"), iso_with_system_cnf("BOOT = cdrom:\\SCES_123.45;1\r\n"))
        write(os.path.join(d, "ff7 (Track 2).bin"), b"audio")
        cue = write(os.path.join(d, "ff7.cue"),
                    'FILE "ff7.bin" BINARY\n  TRACK 01 MODE2/2352\nFILE "ff7 (Track 2).bin" BINARY\n')
        code, out, err = self.run_main("--copy", cue)
        self.assertEqual(code, 0, err)
        for f in ("ff7.cue", "ff7.bin", "ff7 (Track 2).bin"):
            self.assertTrue(os.path.isfile(os.path.join(self.roms, "psx", f)), f)
        self.assertTrue(os.path.isfile(cue))  # copie, pas déplacement

    def test_copy_dry_run_and_move(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.bin"), gba_rom())
        code, out, _ = self.run_main("--copy", "--dry-run", rom)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.roms, "gba", "jeu.bin")))
        code, out, _ = self.run_main("--copy", "--move", rom)
        self.assertTrue(os.path.exists(os.path.join(self.roms, "gba", "jeu.bin")))
        self.assertFalse(os.path.exists(rom))

    def test_copy_conflict(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.bin"), gba_rom())
        write(os.path.join(self.roms, "gba", "jeu.bin"), b"autre contenu")
        code, _o, _e = self.run_main("--copy", rom)
        self.assertEqual(code, L.EXIT_CONFLICT)
        code, _o, _e = self.run_main("--copy", "--rename", rom)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(self.roms, "gba", "jeu (1).bin")))

    def test_copy_identical_is_noop(self):
        rom = write(os.path.join(self.home, "Downloads", "jeu.bin"), gba_rom())
        write(os.path.join(self.roms, "gba", "jeu.bin"), gba_rom())
        code, out, _ = self.run_main("--copy", rom)
        self.assertEqual(code, 0)
        self.assertIn("Identique", out)

    def test_copy_fix_ext(self):
        rom = write(os.path.join(self.home, "Downloads", "Mario Kart.64"), n64_rom("v64"))
        self.write_conf(priority="n64")
        code, _o, err = self.run_main("--copy", "--fix-ext", rom)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(os.path.join(self.roms, "n64", "Mario Kart.v64")))

    def test_related_files(self):
        d = os.path.join(self.tmp, "rel")
        gdi = write(os.path.join(d, "g.gdi"), '3\n1 0 4 2352 track01.bin 0\n2 600 0 2352 "track 02.raw" 0\n')
        m3u = write(os.path.join(d, "g.m3u"), "# disques\ndisc1.chd\ndisc2.chd\n")
        self.assertEqual(L.related_files(gdi), [os.path.join(d, "track01.bin"), os.path.join(d, "track 02.raw")])
        self.assertEqual(L.related_files(m3u), [os.path.join(d, "disc1.chd"), os.path.join(d, "disc2.chd")])


# ==========================================================================
# AppImage : détection, extraction, cache
# ==========================================================================

FAKE_APPIMAGE = r"""#!/bin/sh
echo "$1" >> "{calls}"
if [ "$1" = "--appimage-mount" ]; then
    d="{mnt}"
    mkdir -p "$d/usr/share/es-de/resources/systems/linux" "$d/usr/bin"
    cp "{fix}/es_systems.xml" "{fix}/es_find_rules.xml" "$d/usr/share/es-de/resources/systems/linux/"
    ln -sfn ../share/es-de/resources "$d/usr/bin/resources"
    echo "$d"
    exec sleep 600
fi
exit 1
"""


class TestAppImage(Env):
    def make_fake(self):
        self.app_calls = os.path.join(self.tmp, "app_calls")
        exe = os.path.join(self.home, "Applications", "es-de")
        write(exe, FAKE_APPIMAGE.format(calls=self.app_calls, mnt=os.path.join(self.tmp, "mnt"),
                                        fix=FIXTURES), 0o755)
        link = os.path.join(self.home, ".local", "bin", "es-de")
        os.makedirs(os.path.dirname(link))
        os.symlink(exe, link)
        return exe, link

    def app_call_count(self):
        if not os.path.exists(self.app_calls):
            return 0
        with open(self.app_calls) as fh:
            return len(fh.read().split())

    def test_is_appimage(self):
        p = write(os.path.join(self.tmp, "a"), b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\x00" * 32)
        self.assertTrue(L.is_appimage(p))
        q = write(os.path.join(self.tmp, "b"), b"\x7fELF\x02\x01\x01\x00\x00\x00\x00" + b"\x00" * 32)
        self.assertFalse(L.is_appimage(q))

    def test_internal_path_from_log(self):
        write(os.path.join(self.esde, "logs", "es_log.txt"),
              'Oct 05 14:51:19 Info:   Parsing systems configuration file '
              '"/tmp/.mount_esderemp16509862597217120216/usr/bin/resources/systems/linux/es_systems.xml"...\n')
        self.assertEqual(L.internal_path_from_log(self.esde), "usr/bin/resources/systems/linux")

    def test_extract_and_cache(self):
        exe, link = self.make_fake()
        cache = os.path.join(self.home, ".cache", "es-de-launch")
        res = L.extract_appimage_resources(link, cache, self.esde)
        self.assertEqual(res, os.path.join(cache, "resources"))
        for f in L.RES_FILES:
            self.assertTrue(os.path.isfile(os.path.join(res, f)))
        with open(os.path.join(res, "manifest.json")) as fh:
            m = json.load(fh)
        self.assertEqual(m["executable"], os.path.realpath(exe))
        self.assertEqual(self.app_call_count(), 1)
        # deuxième appel : cache valide, AppImage non sollicitée
        L.extract_appimage_resources(link, cache, self.esde)
        self.assertEqual(self.app_call_count(), 1)
        # exécutable plus récent que le cache : réextraction
        future = time.time() + 60
        os.utime(exe, (future, future))
        L.extract_appimage_resources(link, cache, self.esde)
        self.assertEqual(self.app_call_count(), 2)
        # --refresh-cache
        L.extract_appimage_resources(link, cache, self.esde, force=True)
        self.assertEqual(self.app_call_count(), 3)

    def test_mount_process_is_terminated(self):
        _exe, link = self.make_fake()
        L.extract_appimage_resources(link, os.path.join(self.tmp, "c"), self.esde)
        import subprocess
        out = subprocess.run(["pgrep", "-f", "[s]leep 600"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "")

    def test_end_to_end_with_appimage_conf(self):
        _exe, link = self.make_fake()
        write(self.conf_path, "[install]\ntype = appimage\nexecutable = %s\nconfig_dir = %s\n"
                              "[launch]\nambiguity = fail\n" % (link, self.esde))
        rom = self.rom("n64/Other.z64", n64_rom())
        code, out, err = self.run_main("--output-cmd", rom)
        self.assertEqual(code, 0, err)
        self.assertIn("mupen64plus_next_libretro.so", out)

    def test_init_generates_conf(self):
        os.remove(self.conf_path)
        code, out, err = self.run_main("--init", "--resources-dir", FIXTURES, "--config-dir", self.esde)
        self.assertEqual(code, 0, err)
        conf = L.Conf.load(self.conf_path)
        prio = conf.list("systems", "priority")
        self.assertEqual(prio[0], "n64")  # gamelist le plus fourni
        self.assertEqual(set(prio), {"n64", "snes", "gc"})
        self.assertIn("n64", conf.list("extensions", "zip"))
        self.assertEqual(conf.get("launch", "scripts"), "always")
        code, _o, _e = self.run_main("--init", "--resources-dir", FIXTURES)
        self.assertEqual(code, L.EXIT_USAGE)  # existe déjà, pas de --force

    def test_detect_install_follows_symlink(self):
        exe, link = self.make_fake()
        os.environ["PATH"] = os.path.dirname(link) + ":" + os.environ["PATH"]
        with open(exe, "r+b") as fh:  # en-tête AppImage réel
            content = fh.read()
            fh.seek(0)
            fh.write(b"\x7fELF\x02\x01\x01\x00AI\x02" + content[11:])
        found = L.detect_install()
        self.assertEqual(found[0]["type"], "appimage")
        self.assertEqual(found[0]["real"], os.path.realpath(exe))
        self.assertEqual(found[0]["origin"], "PATH")


# ==========================================================================
# Signatures (détection hors bibliothèque)
# ==========================================================================

class TestSignatures(Env):
    def f(self, name, content):
        return write(os.path.join(self.home, "Downloads", name), content)

    def sniff(self, name, content):
        return L.sniff_file(self.f(name, content))

    def assertSniff(self, name, content, platforms, level=None):
        r = self.sniff(name, content)
        self.assertIsNotNone(r, name)
        self.assertEqual(r[1], platforms, r)
        if level:
            self.assertEqual(r[0], level, r)
        return r

    def test_snes_lorom_hirom_copier(self):
        self.assertSniff("a.sfc", snes_rom(), ["snes"])
        r = self.assertSniff("b.smc", snes_rom(hirom=True, copier=True), ["snes"])
        self.assertIn("HiROM", r[2])
        self.assertIsNone(self.sniff("c.bin", b"\x00" * 0x8000))

    def test_snes_end_to_end(self):
        rom = self.f("jeu.bin", snes_rom())  # .bin : partagé par des dizaines de systèmes
        with self.assertRaises(L.LaunchError) as cm:
            L.detect_system(self.ctx(), rom)
        self.assertIn("sfc, snes, snesna", str(cm.exception))  # même plateforme « snes »
        self.write_conf(priority="snes")
        self.assertEqual(L.detect_system(self.ctx(), rom).system, "snes")

    def test_virtualboy(self):
        self.assertSniff("a.vb", vb_rom(), ["virtualboy"])
        self.assertEqual(L.detect_system(self.ctx(), self.f("jeu.bin", vb_rom())).system, "virtualboy")

    def test_gamecube_wii_compressed(self):
        rvz = bytearray(0x100)
        rvz[0:4] = b"RVZ\x01"
        struct.pack_into(">I", rvz, 0x48, 2)
        self.assertSniff("a.rvz", bytes(rvz), ["wii"], L.CERTAIN)
        wia = bytearray(0x100)
        wia[0:4] = b"WIA\x01"
        wia[0x58 + 0x1C:0x58 + 0x20] = L.GC_MAGIC
        self.assertSniff("b.wia", bytes(wia), ["gc"], L.CERTAIN)
        self.assertSniff("c.gcz", b"\x01\xc0\x0b\xb1" + struct.pack("<I", 1) + b"\x00" * 64, ["wii"])
        ciso = bytearray(0x8100)
        ciso[0:8] = b"CISO" + struct.pack("<I", 0x200000)
        ciso[0x8000 + 0x1C:0x8000 + 0x20] = L.GC_MAGIC
        self.assertSniff("d.ciso", bytes(ciso), ["gc"], L.CERTAIN)
        wbfs = bytearray(0x400)
        wbfs[0:4] = b"WBFS"
        wbfs[0x218:0x21C] = L.WII_MAGIC
        self.assertSniff("e.wbfs", bytes(wbfs), ["wii"], L.CERTAIN)
        self.assertSniff("f.tgc", b"\xae\x0f\x38\xa2" + b"\x00" * 64, ["gc"])

    def test_rvz_end_to_end_breaks_gc_wii_tie(self):
        rvz = bytearray(0x100)
        rvz[0:4] = b"RVZ\x01"
        struct.pack_into(">I", rvz, 0x48, 1)
        det = L.detect_system(self.ctx(), self.f("Mario.rvz", bytes(rvz)))
        self.assertEqual((det.system, det.level), ("gc", L.CERTAIN))

    def test_chd_metadata(self):
        self.assertSniff("a.chd", chd_v5(b"CHGD"), ["dreamcast", "arcade"])
        self.assertSniff("b.chd", chd_v5(b"DVD "), ["ps2", "psp"])
        r = self.sniff("c.chd", chd_v5(b"CHT2"))
        self.assertEqual(r[0], "INFO")
        self.assertIn("CD", r[2])

    def test_chd_dvd_end_to_end(self):
        rom = self.f("jeu.chd", chd_v5(b"DVD "))
        with self.assertRaises(L.LaunchError) as cm:
            L.detect_system(self.ctx(), rom)
        self.assertIn("ps2, psp", str(cm.exception))
        self.write_conf(extra="[extensions]\nchd = ps2, psx\n")
        self.assertEqual(L.detect_system(self.ctx(), rom).system, "ps2")

    def test_psp_iso_and_cso(self):
        iso = iso_image({"UMD_DATA.BIN": b"ULES-00000"}, system_id="PSP GAME")
        self.assertSniff("a.iso", iso, ["psp"], L.CERTAIN)
        r = self.assertSniff("b.cso", make_cso(iso), ["psp"], L.CERTAIN)
        self.assertTrue(r[2].startswith("CSO"))
        det = L.detect_system(self.ctx(), self.f("jeu.cso", make_cso(iso)))
        self.assertEqual(det.system, "psp")

    def test_neogeo_cd(self):
        self.assertSniff("a.iso", iso_image({"IPL.TXT": b"PRG"}), ["neogeocd"], L.CERTAIN)

    def test_xbox_xiso(self):
        data = bytearray(0x10100)
        data[0x10000:0x10014] = b"MICROSOFT*XBOX*MEDIA"
        self.assertSniff("a.iso", bytes(data), ["xbox"], L.CERTAIN)

    def test_dreamcast_gdi(self):
        d = os.path.join(self.home, "Downloads", "dc")
        write(os.path.join(d, "track01.bin"), b"\x00" * 16 + b"SEGA SEGAKATANA " + b"\x00" * 64)
        gdi = write(os.path.join(d, "jeu.gdi"), "1\n1 0 4 2352 track01.bin 0\n")
        r = L.sniff_file(gdi)
        self.assertEqual(r[1], ["dreamcast"])
        self.assertEqual(L.detect_system(self.ctx(), gdi).system, "dreamcast")  # .gdi : dreamcast et fmtowns

    def test_sega_handhelds_and_others(self):
        sms = bytearray(0x8000)
        sms[0x7FF0:0x7FF8] = b"TMR SEGA"
        sms[0x7FFF] = 0x4C
        self.assertSniff("a.sms", bytes(sms), ["mastersystem"])
        sms[0x7FFF] = 0x7C
        self.assertSniff("b.gg", bytes(sms), ["gamegear"])
        self.assertSniff("c.lnx", b"LYNX\x00" + b"\x00" * 64, ["atarilynx"])
        self.assertSniff("d.a78", b"\x01ATARI7800" + b"\x00" * 64, ["atari7800"])
        ngp = bytearray(b"COPYRIGHT BY SNK CORPORATION" + b"\x00" * 64)
        ngp[0x23] = 0x10
        self.assertSniff("e.ngc", bytes(ngp), ["ngpc"])
        self.assertSniff("f.bin", b"\x01ZZZZZ\x01" + b"\x00" * 64, ["3do"])
        self.assertSniff("g.exe", b"PS-X EXE" + b"\x00" * 64, ["psx"])
        self.assertSniff("h.nro", b"\x00" * 0x10 + b"NRO0" + b"\x00" * 64, ["switch"])
        self.assertSniff("i.3dsx", b"3DSX" + b"\x00" * 64, ["n3ds"])
        self.assertSniff("j.bin", b"\x00" * 0x800 + b"PC Engine CD-ROM SYSTEM" + b"\x00" * 64, ["pcenginecd"])

    def test_7z_content(self):
        p = self.f("jeu.7z", b"7z\xbc\xaf\x27\x1c")
        orig = (L.list_7z, L.read_7z_member)
        try:
            L.list_7z = lambda path: [("Jeu (Europe).gba", 0x200), ("lisezmoi.txt", 10)]
            L.read_7z_member = lambda path, name, n: gba_rom()
            r = L.sniff_file(p)
            self.assertEqual(r[1], ["gba"])
            self.assertEqual(L.detect_system(self.ctx(), p).system, "gba")
        finally:
            L.list_7z, L.read_7z_member = orig

    def test_7z_listing_parser(self):
        fake = os.path.join(self.bin, "7z")
        write(fake, "#!/bin/sh\nprintf 'Path = a.sfc\\nSize = 524288\\nAttributes = A\\n\\n"
                    "Path = sub\\nSize = 0\\nAttributes = D\\n\\n'\n", 0o755)
        self.assertEqual(L.list_7z(self.f("x.7z", b"7z")), [("a.sfc", 524288)])


# ==========================================================================
# Types d'installation (arborescence système simulée via L.ROOT)
# ==========================================================================

ELF_PLAIN = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 0x2000          # binaire ordinaire
ELF_APPIMAGE = b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\x00" * 0x2000   # AppImage standard
ELF_URUNTIME = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 0x3000 + b"hsqs" + b"\x00" * 64  # runtime sans « AI »


class TestInstallTypes(Env):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(self.root)
        self.old_root = L.ROOT
        L.ROOT = self.root

    def tearDown(self):
        L.ROOT = self.old_root
        super().tearDown()

    def resources(self, d):
        os.makedirs(d, exist_ok=True)
        for f in L.RES_FILES:
            shutil.copyfile(os.path.join(FIXTURES, f), os.path.join(d, f))
        return d

    def auto_conf(self):
        write(self.conf_path, "[install]\ntype = auto\nconfig_dir = %s\n[launch]\nambiguity = fail\n" % self.esde)
        return L.Conf.load(self.conf_path)

    def resolve(self):
        ns = L.argparse.Namespace(config_dir=None, resources_dir=None, refresh_cache=False)
        return L.resolve_resources(self.auto_conf(), ns, self.esde)[:2]

    def only(self, kind):
        found = L.detect_install()
        self.assertTrue(found, "aucune installation détectée")
        self.assertEqual(found[0]["type"], kind, found)
        return found[0]

    def test_system_package(self):
        write(os.path.join(self.root, "usr/bin/es-de"), ELF_PLAIN, 0o755)
        res = self.resources(os.path.join(self.root, "usr/share/es-de/resources/systems/linux"))
        c = self.only("system")
        self.assertTrue(c["valid"])
        self.assertEqual(self.resolve(), (res, "system"))

    def test_local_build_in_home(self):
        write(os.path.join(self.home, ".local/bin/es-de"), ELF_PLAIN, 0o755)
        res = self.resources(os.path.join(self.home, ".local/share/es-de/resources/systems/linux"))
        self.only("local")
        self.assertEqual(self.resolve(), (res, "local"))

    def test_usr_local_build(self):
        write(os.path.join(self.root, "usr/local/bin/es-de"), ELF_PLAIN, 0o755)
        res = self.resources(os.path.join(self.root, "usr/local/share/es-de/resources/systems/linux"))
        self.only("local")
        self.assertEqual(self.resolve()[0], res)

    def test_portable_via_path(self):
        d = os.path.join(self.home, "Jeux", "ES-DE")
        write(os.path.join(d, "es-de"), ELF_PLAIN, 0o755)
        res = self.resources(os.path.join(d, "resources/systems/linux"))
        os.environ["PATH"] = d + ":" + os.environ["PATH"]
        c = self.only("portable")
        self.assertEqual(c["origin"], "PATH")
        self.assertEqual(self.resolve(), (res, "portable"))

    def test_flatpak_system_and_user(self):
        res = self.resources(os.path.join(
            self.root, "var/lib/flatpak/app/org.es_de.frontend/current/active/files/share/es-de/resources/systems/linux"))
        write(os.path.join(self.root, "var/lib/flatpak/exports/bin/org.es_de.frontend"), "#!/bin/sh\n", 0o755)
        c = self.only("flatpak")
        self.assertEqual(c["origin"], "flatpak org.es_de.frontend")
        self.assertTrue(c["executable"].endswith("exports/bin/org.es_de.frontend"))
        self.assertEqual(self.resolve(), (res, "flatpak"))
        shutil.rmtree(os.path.join(self.root, "var"))
        res2 = self.resources(os.path.join(
            self.home, ".local/share/flatpak/app/org.es_de.frontend/current/active/files/share/es-de/resources/systems/linux"))
        self.assertEqual(self.resolve(), (res2, "flatpak"))

    def test_snap(self):
        res = self.resources(os.path.join(self.root, "snap/es-de/current/usr/share/es-de/resources/systems/linux"))
        c = self.only("snap")
        self.assertEqual(c["origin"], "snap es-de")
        self.assertEqual(self.resolve(), (res, "snap"))

    def test_sandbox_warning(self):
        self.resources(os.path.join(self.root, "snap/es-de/current/usr/share/es-de/resources/systems/linux"))
        self.auto_conf()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ctx = L.build_context(L.Conf.load(self.conf_path),
                                  L.argparse.Namespace(config_dir=None, resources_dir=None, refresh_cache=False))
        self.assertEqual(len(ctx.systems), 195)
        self.assertIn("aucune version de ce type", err.getvalue())

    def test_wrapper_script_is_not_an_install(self):
        # Cas réel : ~/.local/bin/start-es-de (script) à côté de l'AppImage
        write(os.path.join(self.home, "Applications/es-de"), ELF_APPIMAGE, 0o755)
        write(os.path.join(self.home, "Applications/es-de_3.4.1.OLD"), ELF_APPIMAGE, 0o755)
        write(os.path.join(self.home, ".local/bin/start-es-de"), "#!/bin/sh\nexec es-de\n", 0o755)
        found = L.detect_install()
        self.assertEqual([os.path.basename(c["executable"]) for c in found], ["es-de", "es-de_3.4.1.OLD"])

    def test_appimage_standard_and_nonstandard_runtime(self):
        a = write(os.path.join(self.home, "Applications/ES-DE_x64.AppImage"), ELF_APPIMAGE, 0o755)
        self.assertTrue(L.is_appimage(a))
        b = write(os.path.join(self.tmp, "es-de"), ELF_URUNTIME, 0o755)
        self.assertTrue(L.is_appimage(b))  # runtime sans signature « AI » (cas de l'auteur)
        self.assertFalse(L.is_appimage(write(os.path.join(self.tmp, "plain"), ELF_PLAIN, 0o755)))
        self.only("appimage")

    def test_invalid_first_candidate_is_skipped(self):
        # binaire système sans ressources + Flatpak exploitable
        write(os.path.join(self.root, "usr/bin/es-de"), ELF_PLAIN, 0o755)
        res = self.resources(os.path.join(
            self.root, "var/lib/flatpak/app/org.es_de.frontend/current/active/files/share/es-de/resources/systems/linux"))
        found = L.detect_install()
        self.assertEqual([c["type"] for c in found], ["flatpak", "system"])
        self.assertEqual(self.resolve(), (res, "flatpak"))

    def test_nothing_found(self):
        with self.assertRaises(L.LaunchError) as cm:
            self.resolve()
        self.assertEqual(cm.exception.code, L.EXIT_CONFIG)

    def test_resources_from_log(self):
        res = self.resources(os.path.join(self.root, "usr/share/es-de/resources/systems/linux"))
        write(os.path.join(self.esde, "logs/es_log.txt"),
              'Info: Parsing systems configuration file "/usr/share/es-de/resources/systems/linux/es_systems.xml"...\n')
        self.assertEqual(L.resources_from_log(self.esde), res)
        self.assertEqual(self.resolve(), (res, "system"))  # trouvé sans binaire

    def test_flatpak_path_from_log(self):
        res = self.resources(os.path.join(
            self.root, "var/lib/flatpak/app/org.es_de.frontend/current/active/files/share/es-de/resources/systems/linux"))
        write(os.path.join(self.esde, "logs/es_log.txt"),
              'Parsing systems configuration file "/app/share/es-de/resources/systems/linux/es_systems.xml"...\n')
        self.assertEqual(L.resources_from_log(self.esde), res)

    def test_appimage_log_path_is_not_used_directly(self):
        write(os.path.join(self.esde, "logs/es_log.txt"),
              'Parsing systems configuration file "/tmp/.mount_esde123/usr/bin/resources/systems/linux/es_systems.xml"\n')
        self.assertIsNone(L.resources_from_log(self.esde))

    def test_stale_executable_in_conf_falls_back(self):
        write(os.path.join(self.root, "usr/bin/es-de"), ELF_PLAIN, 0o755)
        res = self.resources(os.path.join(self.root, "usr/share/es-de/resources/systems/linux"))
        write(self.conf_path, "[install]\ntype = appimage\nexecutable = ~/Applications/disparu.AppImage\n"
                              "config_dir = %s\n" % self.esde)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = L.resolve_resources(L.Conf.load(self.conf_path),
                                      L.argparse.Namespace(resources_dir=None, refresh_cache=False), self.esde)[:2]
        self.assertEqual(got, (res, "system"))
        self.assertIn("introuvable", err.getvalue())

    def test_config_dir_in_flatpak_data(self):
        shutil.rmtree(self.esde)
        d = os.path.join(self.home, ".var/app/org.es_de.frontend/data/ES-DE")
        write(os.path.join(d, "settings/es_settings.xml"), SETTINGS.format(scripts="true"))
        self.assertEqual(L.detect_config_dir(), d)

    def test_detect_install_cli(self):
        write(os.path.join(self.root, "usr/bin/es-de"), ELF_PLAIN, 0o755)
        self.resources(os.path.join(self.root, "usr/share/es-de/resources/systems/linux"))
        self.resources(os.path.join(self.root, "snap/es-de/current/usr/share/es-de/resources/systems/linux"))
        code, out, _err = self.run_main("--detect-install")
        self.assertEqual(code, 0)
        self.assertIn("* system", out)
        self.assertIn("  snap", out)
        code, out, _err = self.run_main("--detect-install", "--json")
        self.assertEqual([c["type"] for c in json.loads(out)["installs"]], ["system", "snap"])

    def test_init_for_system_package(self):
        write(os.path.join(self.root, "usr/bin/es-de"), ELF_PLAIN, 0o755)
        res = self.resources(os.path.join(self.root, "usr/share/es-de/resources/systems/linux"))
        os.remove(self.conf_path)
        code, _out, err = self.run_main("--init")
        self.assertEqual(code, 0, err)
        conf = L.Conf.load(self.conf_path)
        self.assertEqual(conf.get("install", "type"), "system")
        self.assertEqual(conf.get("install", "resources_dir"), res)
        self.assertEqual(conf.list("systems", "priority")[0], "n64")


if __name__ == "__main__":
    unittest.main()
