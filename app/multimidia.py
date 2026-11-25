#!/usr/bin/env python3
"""
Central multimidia da Caixinha - tela 1280x720 vista pelo Moonlight / navegador.

 - Inicio: relogio grande, data e atalhos redondos (Radio, YT Music, Bluetooth, Ajustes)
 - Bluetooth: seletor de reprodutor (MPRIS), saida de audio, aparelhos, procurar/parear
 - Ajustes: saida do MPD (se instalado), informacoes
 - Barra de baixo (layer-shell, sempre visivel): HOME, VOL -/+, faixa atual, anterior/play/proxima, Bluetooth, Ajustes
 - Pedido de pareamento (PIN) aparece como janela dentro do app, por cima de tudo.

Os apps abrem em tela cheia, cada um no seu espaco do sway; HOME volta para o inicio.
Estado e comandos de audio/Bluetooth: API local do caixinha_bt.py (127.0.0.1:8095).
"""
import json
import os
import socket
import shutil
import subprocess
import threading
import time
import urllib.request

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LS
except (ValueError, ImportError):
    LS = None
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

API = "http://127.0.0.1:8095"
ICONES = "Material Icons"
DIAS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]

# atalhos da tela inicial: id, nome, icone (ligadura Material Icons), cor, comando, app_id no sway
APPS = [
    ("radio", "Rádio", "radio", "#f26b1d", ["shortwave"], "de.haeckerfelix.Shortwave"),
    ("ytmusic", "YT Music", "smart_display", "#e62117",
     ["youtube-music", "--ozone-platform=wayland", "--enable-features=UseOzonePlatform"], "youtube-music"),
    ("bluetooth", "Bluetooth", "bluetooth", "#2f6fe4", None, None),
    ("ajustes", "Ajustes", "settings", "#5b6b8c", None, None),
]
ICONE_APP = {  # icone do proprio app, se instalado (fica dentro do circulo)
    "ytmusic": ["/usr/share/icons/hicolor/512x512/apps/youtube-music.png",
                "/usr/share/icons/hicolor/256x256/apps/youtube-music.png"],
    "radio": ["/usr/share/icons/hicolor/scalable/apps/de.haeckerfelix.Shortwave.svg"],
}

CSS = """
* { font-family: "Noto Sans"; }
window.principal {
  background-image:
    radial-gradient(circle at 78% 18%, alpha(#9cc0ff, 0.30), transparent 32%),
    radial-gradient(circle at 62% 60%, alpha(#6f8cff, 0.20), transparent 28%),
    radial-gradient(circle at 15% 85%, alpha(#2a6bff, 0.40), transparent 40%),
    linear-gradient(135deg, #071a52 0%, #0f3596 50%, #0a2368 100%);
  color: white;
}
.icone { font-family: "Material Icons"; font-weight: normal; }
.relogio { font-size: 170px; font-weight: 200; letter-spacing: -4px; }
.data { font-size: 26px; font-weight: 300; color: alpha(white, 0.85); }
.topo-icone { font-size: 30px; color: alpha(white, 0.85); }
.tile { background: none; border: none; box-shadow: none; padding: 10px 14px; border-radius: 24px; color: white; }
.tile:hover { background: alpha(white, 0.06); }
.tile:active { background: alpha(white, 0.14); }
.circulo { min-width: 112px; min-height: 112px; border-radius: 999px; padding: 0;
           box-shadow: 0 6px 18px alpha(black, 0.35); }
.circulo .icone { font-size: 58px; color: white; }
.tile-nome { font-size: 22px; font-weight: 400; margin-top: 12px; color: white; }
.pontos { color: alpha(white, 0.6); font-size: 14px; }

.pagina-titulo { font-size: 30px; font-weight: 300; }
.cartao { background: alpha(#06143d, 0.55); border-radius: 22px; padding: 18px; }
.cartao-titulo { font-size: 15px; font-weight: 600; color: alpha(white, 0.6); letter-spacing: 1px; }
.faixa { font-size: 28px; font-weight: 500; }
.artista { font-size: 19px; color: alpha(white, 0.75); }
.capa { border-radius: 16px; background: alpha(white, 0.08); }
.redondo { min-width: 64px; min-height: 64px; border-radius: 999px; padding: 0;
           background: alpha(white, 0.12); border: none; box-shadow: none; color: white; }
.redondo:hover { background: alpha(white, 0.2); }
.redondo .icone { font-size: 36px; }
.redondo.grande { min-width: 84px; min-height: 84px; background: white; color: #0d2f86; }
.redondo.grande .icone { font-size: 52px; }
.redondo.pequeno { min-width: 48px; min-height: 48px; }
.redondo.pequeno .icone { font-size: 26px; }
.redondo.ativo { background: #2f6fe4; }
.chip { border-radius: 999px; padding: 6px 16px; background: alpha(white, 0.10); border: none;
        box-shadow: none; color: white; font-size: 16px; }
.chip.ativo { background: white; color: #0d2f86; font-weight: 600; }
.linha { padding: 8px 4px; }
.linha-nome { font-size: 19px; }
.linha-sub { font-size: 14px; color: alpha(white, 0.6); }
.botao { border-radius: 999px; padding: 8px 20px; background: alpha(white, 0.14); border: none;
         box-shadow: none; color: white; font-size: 16px; font-weight: 500; }
.botao:hover { background: alpha(white, 0.22); }
.botao.azul { background: #2f6fe4; }
.botao.vermelho { background: #d6453d; }
.vazio { color: alpha(white, 0.5); font-size: 16px; }
scale trough { min-height: 8px; border-radius: 999px; background: alpha(white, 0.18); }
scale highlight { border-radius: 999px; background: white; }
scale slider { min-width: 26px; min-height: 26px; border-radius: 999px; background: white; border: none; }
switch { border-radius: 999px; background: alpha(white, 0.2); }
switch:checked { background: #2f6fe4; }
switch slider { border-radius: 999px; background: white; min-width: 26px; min-height: 26px; }
entry { border-radius: 14px; background: alpha(white, 0.12); color: white; padding: 8px 14px; font-size: 18px;
        border: none; box-shadow: none; }

.fundo-pin { background: alpha(black, 0.55); }
.janela-pin { background: #0d2a74; border-radius: 26px; padding: 30px 40px; box-shadow: 0 10px 40px alpha(black, 0.6); }
.pin-codigo { font-size: 64px; font-weight: 300; letter-spacing: 10px; }
.pin-texto { font-size: 21px; }

window.flutuante { background: transparent; }
.painel-flutuante { background: alpha(#0a1d58, 0.97); border-radius: 24px; padding: 18px 18px 14px 18px;
                    box-shadow: 0 12px 40px alpha(black, 0.55); color: white; border: 1px solid alpha(white, 0.08); }
.capa-mini { border-radius: 12px; background: alpha(white, 0.08); }
.faixa-mini { font-size: 18px; font-weight: 500; }
.artista-mini { font-size: 14px; color: alpha(white, 0.7); }
.linha-nome-mini { font-size: 16px; }
.painel-flutuante .linha { padding: 3px 2px; }
.painel-flutuante scrollbar slider { min-width: 6px; border-radius: 999px; background: alpha(white, 0.35); }
.painel-flutuante scrollbar { background: transparent; }
.painel-flutuante .botao { padding: 6px 14px; font-size: 14px; }
window.barra { background: alpha(#040d2b, 0.94); color: white; }
.barra .redondo { min-width: 50px; min-height: 50px; background: alpha(white, 0.08); }
.barra .redondo .icone { font-size: 28px; }
.barra-home { border-radius: 14px; padding: 4px 22px; min-width: 64px; background: alpha(white, 0.10); border: none;
              box-shadow: none; color: white; font-size: 15px; font-weight: 700; letter-spacing: 1px; }
.barra-vol { font-size: 15px; min-width: 52px; color: alpha(white, 0.85); }
.barra-faixa { font-size: 16px; }
.barra-relogio { font-size: 17px; font-weight: 500; }
"""


# ---------------------------------------------------------------- utilidades
def icone(nome, classe=None):
    l = Gtk.Label(label=nome)
    l.add_css_class("icone")
    if classe:
        l.add_css_class(classe)
    return l


def botao_redondo(nome, cb, *classes):
    b = Gtk.Button()
    b.set_child(icone(nome))
    b.add_css_class("redondo")
    for c in classes:
        b.add_css_class(c)
    b.connect("clicked", lambda *_: cb())
    b.set_valign(Gtk.Align.CENTER)
    b.set_halign(Gtk.Align.CENTER)
    return b


def api_get():
    try:
        with urllib.request.urlopen(API + "/estado", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return None


def api_cmd(comando, valor=""):
    def _f():
        try:
            req = urllib.request.Request(API + "/comando", data=json.dumps({"comando": comando, "valor": valor}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:
            print("api:", e, flush=True)
    threading.Thread(target=_f, daemon=True).start()


def sway(*args):
    try:
        return subprocess.run(["swaymsg", *args], capture_output=True, text=True, timeout=4).stdout
    except Exception:
        return ""


def app_ids_abertos():
    ids = set()

    def andar(n):
        if n.get("app_id"):
            ids.add(n["app_id"])
        cls = (n.get("window_properties") or {}).get("class")
        if cls:
            ids.add(cls)
        for f in n.get("nodes", []) + n.get("floating_nodes", []):
            andar(f)
    try:
        andar(json.loads(sway("-t", "get_tree") or "{}"))
    except ValueError:
        pass
    return ids


def ip_local():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "?"


def limpa(container):
    c = container.get_first_child()
    while c:
        n = c.get_next_sibling()
        container.remove(c)
        c = n


# ---------------------------------------------------------------- MPRIS (reprodutores)
class Mpris:
    def __init__(self, ao_mudar):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.ao_mudar = ao_mudar
        self.escolhido = None
        self.players = {}  # nome dbus -> dict
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                  "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE,
                                  lambda *a: GLib.timeout_add(400, self.atualiza), None)
        self.bus.signal_subscribe(None, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                  "/org/mpris/MediaPlayer2", None, Gio.DBusSignalFlags.NONE,
                                  lambda *a: GLib.idle_add(self.atualiza), None)
        GLib.timeout_add_seconds(3, lambda: (self.atualiza(), True)[1])
        self.atualiza()

    def _nomes(self):
        try:
            r = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "ListNames", None, None, Gio.DBusCallFlags.NONE, 1000, None)
            return [n for n in r.unpack()[0] if n.startswith("org.mpris.MediaPlayer2.")]
        except GLib.Error:
            return []

    def _prop(self, nome, iface, prop):
        try:
            r = self.bus.call_sync(nome, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                                   GLib.Variant("(ss)", (iface, prop)), None, Gio.DBusCallFlags.NONE, 800, None)
            return r.unpack()[0]
        except GLib.Error:
            return None

    @staticmethod
    def _nome_amigavel(dbus_nome, ident):
        if "media_player" in dbus_nome.lower() or "media player" in str(ident).lower():
            # celular por Bluetooth (mpris-proxy / AVRCP): "Galaxy do Ze Media Player" -> "Galaxy do Ze"
            nome = str(ident).replace("_", " ")
            return nome.replace("Media Player", "").strip() or "Celular"
        n = (dbus_nome + " " + str(ident)).lower()
        for chave, nome in (("shortwave", "Rádio"), ("youtube", "YT Music"), ("chromium", "YT Music"),
                            ("mpd", "Music Assistant")):
            if chave in n:
                return nome
        return ident

    # -- MPD (Music Assistant / HA): nao tem MPRIS, e lido direto pelo socket
    @staticmethod
    def _mpd(*cmds):
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 6600), timeout=1) as s:
                f = s.makefile("rw", encoding="utf8", newline="\n")
                f.readline()
                out = {}
                for c in cmds:
                    f.write(c + "\n"); f.flush()
                    while True:
                        l = f.readline()
                        if not l or l.startswith("OK") or l.startswith("ACK"):
                            break
                        k, _, v = l.rstrip("\n").partition(": ")
                        out.setdefault(k, v)
                return out
        except OSError:
            return None

    def _ler_mpd(self):
        d = self._mpd("status", "currentsong")
        if d is None:
            return None
        estado = {"play": "Playing", "pause": "Paused"}.get(d.get("state"), "Stopped")
        titulo = d.get("Title") or d.get("Name") or (os.path.basename(d["file"]) if d.get("file") else "")
        if estado == "Stopped" and not titulo:
            return None  # MPD parado e vazio: nao aparece como reprodutor
        return {"nome": "Music Assistant", "estado": estado, "titulo": titulo,
                "artista": d.get("Artist") or d.get("Album") or "", "capa": ""}

    def atualiza(self):
        novos = {}
        for n in self._nomes():
            md = self._prop(n, "org.mpris.MediaPlayer2.Player", "Metadata") or {}
            ident = self._prop(n, "org.mpris.MediaPlayer2", "Identity") or n.rsplit(".", 1)[-1]
            artista = md.get("xesam:artist") or []
            novos[n] = {
                "nome": self._nome_amigavel(n, ident),
                "estado": self._prop(n, "org.mpris.MediaPlayer2.Player", "PlaybackStatus") or "Stopped",
                "titulo": md.get("xesam:title") or "",
                "artista": ", ".join(artista) if isinstance(artista, list) else str(artista),
                "capa": md.get("mpris:artUrl") or "",
            }
        mpd = self._ler_mpd()
        if mpd:
            novos["mpd"] = mpd

        # um reprodutor por vez: quem acabou de comecar a tocar pausa os outros
        comecou = [k for k, v in novos.items()
                   if v["estado"] == "Playing" and self.players.get(k, {}).get("estado") != "Playing"]
        if comecou and self.players:  # na 1a leitura (central recem-aberta) nao pausa ninguem
            novo = comecou[-1]
            self.escolhido = novo
            for k, v in novos.items():
                if k != novo and v["estado"] == "Playing":
                    self._comando(k, "Pause")
                    v["estado"] = "Paused"

        if novos != self.players:
            self.players = novos
            if self.escolhido not in novos:
                tocando = [k for k, v in novos.items() if v["estado"] == "Playing"]
                self.escolhido = tocando[0] if tocando else (next(iter(novos)) if novos else None)
            self.ao_mudar()
        return False

    def atual(self):
        tocando = [k for k, v in self.players.items() if v["estado"] == "Playing"]
        if self.escolhido in self.players and (self.players[self.escolhido]["estado"] == "Playing" or not tocando):
            return self.escolhido
        return tocando[0] if tocando else self.escolhido

    def _comando(self, n, metodo):
        if n == "mpd":
            st = (self.players.get("mpd") or {}).get("estado")
            cmd = {"Pause": "pause 1", "Next": "next", "Previous": "previous",
                   "PlayPause": "pause 1" if st == "Playing" else "play"}.get(metodo)
            if cmd:
                self._mpd(cmd)
            return
        self.bus.call(n, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", metodo,
                      None, None, Gio.DBusCallFlags.NONE, 2000, None, None, None)

    def chama(self, metodo):
        n = self.atual()
        if n:
            self._comando(n, metodo)
            GLib.timeout_add(500, self.atualiza)


# ---------------------------------------------------------------- painel de audio (pagina e flutuante)
def linha_aparelho(nome, sub, botoes, compacto=False):
    l = Gtk.Box(spacing=8)
    l.add_css_class("linha")
    tx = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
    a = Gtk.Label(label=nome, xalign=0, ellipsize=Pango.EllipsizeMode.END)
    a.add_css_class("linha-nome-mini" if compacto else "linha-nome")
    b = Gtk.Label(label=sub, xalign=0)
    b.add_css_class("linha-sub")
    tx.append(a)
    tx.append(b)
    l.append(tx)
    for item in botoes:
        l.append(item)
    return l


def titulo_cartao(texto, **kw):
    t = Gtk.Label(label=texto, xalign=0, **kw)
    t.add_css_class("cartao-titulo")
    return t


class PainelAudio:
    """Reprodutor + volume + saida + aparelhos Bluetooth.
    compacto=False: pagina inteira (2 cartoes); compacto=True: painel flutuante do canto (estilo ChromeOS)."""

    def __init__(self, app, compacto):
        self.app, self.compacto = app, compacto
        self.assin = {}
        self.capa_url = None
        if compacto:
            self._monta_compacto()
        else:
            self._monta_pagina()

    # -- partes comuns
    def _chips(self, por_linha):
        return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=por_linha,
                           min_children_per_line=1, column_spacing=8, row_spacing=8)

    def _volume(self):
        vol = Gtk.Box(spacing=12)
        vol.append(icone("volume_up", "topo-icone"))
        self.vol = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.vol.set_hexpand(True)
        self.vol.set_draw_value(False)
        self.vol.connect("value-changed", self.app._vol_mexeu)
        vol.append(self.vol)
        return vol

    def _cab_aparelhos(self):
        cab = Gtk.Box(spacing=10, margin_top=4)
        cab.append(titulo_cartao("APARELHOS", hexpand=True))
        vis = Gtk.Label(label="Visível")
        vis.add_css_class("linha-sub")
        cab.append(vis)
        self.sw_visivel = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.sw_visivel.connect("state-set", lambda _s, v: self.app._chave("visivel", v))
        cab.append(self.sw_visivel)
        self.bt_procurar = Gtk.Button(label="Procurar")
        self.bt_procurar.add_css_class("botao")
        self.bt_procurar.add_css_class("azul")
        self.bt_procurar.connect("clicked", lambda *_: api_cmd("procurar"))
        cab.append(self.bt_procurar)
        return cab

    def _controles(self, *classes):
        m = lambda metodo: self.app.mpris.chama(metodo)
        ctl = Gtk.Box(spacing=18 if self.compacto else 26, halign=Gtk.Align.CENTER)
        ctl.append(botao_redondo("skip_previous", lambda: m("Previous"), *classes))
        self.bt_play = botao_redondo("play_arrow", lambda: m("PlayPause"), *(classes or ("grande",)))
        ctl.append(self.bt_play)
        ctl.append(botao_redondo("skip_next", lambda: m("Next"), *classes))
        return ctl

    # -- pagina inteira (como era)
    def _monta_pagina(self):
        raiz = Gtk.Box(spacing=22, margin_top=24, margin_bottom=20, margin_start=28, margin_end=28)
        esq = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, hexpand=True)
        esq.add_css_class("cartao")
        esq.append(titulo_cartao("REPRODUTOR"))
        self.chips = self._chips(4)
        esq.append(self.chips)
        corpo = Gtk.Box(spacing=22, vexpand=True, valign=Gtk.Align.CENTER)
        self.capa = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.COVER)
        self.capa.set_size_request(190, 190)
        self.capa.add_css_class("capa")
        corpo.append(self.capa)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER, hexpand=True)
        self.faixa = Gtk.Label(xalign=0, wrap=True, max_width_chars=22, lines=2, ellipsize=Pango.EllipsizeMode.END)
        self.faixa.add_css_class("faixa")
        self.artista = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.artista.add_css_class("artista")
        info.append(self.faixa)
        info.append(self.artista)
        corpo.append(info)
        esq.append(corpo)
        c = self._controles()
        c.set_margin_bottom(6)
        esq.append(c)
        raiz.append(esq)

        dir_ = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, hexpand=True)
        dir_.add_css_class("cartao")
        dir_.append(titulo_cartao("SAÍDA DE ÁUDIO"))
        self.saidas = self._chips(3)
        dir_.append(self.saidas)
        dir_.append(self._volume())
        dir_.append(self._cab_aparelhos())
        self.aparelhos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, vexpand=True)
        dir_.append(self.aparelhos)
        raiz.append(dir_)
        self.raiz = raiz

    # -- painel flutuante (canto inferior direito, acima da barra)
    def _monta_compacto(self):
        p = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.END,
                    valign=Gtk.Align.END, margin_end=14, margin_bottom=10, width_request=460)
        p.add_css_class("painel-flutuante")
        topo = Gtk.Box(spacing=8)
        topo.append(titulo_cartao("REPRODUTOR", hexpand=True, valign=Gtk.Align.CENTER))
        exp = botao_redondo("open_in_full", self.app.expandir, "pequeno")
        exp.set_tooltip_text("Abrir em tela cheia")
        topo.append(exp)
        topo.append(botao_redondo("close", self.app.fechar_flutuante, "pequeno"))
        p.append(topo)
        self.chips = self._chips(3)
        p.append(self.chips)

        tocando = Gtk.Box(spacing=14)
        self.capa = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.COVER)
        self.capa.set_size_request(76, 76)
        self.capa.add_css_class("capa-mini")
        tocando.append(self.capa)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
        self.faixa = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=26)
        self.faixa.add_css_class("faixa-mini")
        self.artista = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=30)
        self.artista.add_css_class("artista-mini")
        info.append(self.faixa)
        info.append(self.artista)
        tocando.append(info)
        p.append(tocando)
        p.append(self._controles("pequeno"))
        p.append(self._volume())

        p.append(titulo_cartao("SAÍDA DE ÁUDIO", margin_top=4))
        self.saidas = self._chips(3)
        p.append(self.saidas)
        p.append(self._cab_aparelhos())
        self.aparelhos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        rolagem = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                     vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                     propagate_natural_height=True, max_content_height=170)
        rolagem.set_child(self.aparelhos)
        p.append(rolagem)
        self.raiz = p

    # -- atualizacao (so redesenha o que mudou: sem piscar)
    def _mudou(self, chave, valor):
        s = json.dumps(valor, sort_keys=True)
        if self.assin.get(chave) == s:
            return False
        self.assin[chave] = s
        return True

    def aplica(self, e):
        if e.get("volume") is not None and self._mudou("vol", e["volume"]) and not getattr(self.app, "_vol_timer", None):
            self.app._mexendo_vol = True
            self.vol.set_value(e["volume"])
            self.app._mexendo_vol = False
        if self._mudou("saidas", [e.get("saidas"), e.get("saida_atual")]):
            limpa(self.saidas)
            for s in e.get("saidas", []):
                b = Gtk.Button(label=s["nome"])
                b.add_css_class("chip")
                if s["id"] == e.get("saida_atual"):
                    b.add_css_class("ativo")
                b.connect("clicked", lambda _b, i=s["id"]: api_cmd("saida", i))
                self.saidas.append(b)
        if self._mudou("aparelhos", [e.get("pareados"), e.get("proximos"), e.get("conectado"), e.get("procurando")]):
            limpa(self.aparelhos)
            conectado = (e.get("conectado") or {}).get("mac")
            for d in e.get("pareados", []):
                if d["mac"] == conectado:
                    acao = Gtk.Button(label="Desconectar")
                    acao.connect("clicked", lambda *_: api_cmd("desconectar"))
                    sub = "Conectado"
                else:
                    acao = Gtk.Button(label="Conectar")
                    acao.add_css_class("azul")
                    acao.connect("clicked", lambda _b, m=d["mac"]: api_cmd("conectar", m))
                    sub = "Pareado"
                acao.add_css_class("botao")
                esquecer = botao_redondo("delete", lambda m=d["mac"]: api_cmd("esquecer", m), "pequeno")
                self.aparelhos.append(linha_aparelho(d["nome"], sub, [acao, esquecer], self.compacto))
            for d in e.get("proximos", [])[:4 if self.compacto else 6]:
                b = Gtk.Button(label="Parear")
                b.add_css_class("botao")
                b.connect("clicked", lambda _b, m=d["mac"]: api_cmd("parear", m))
                self.aparelhos.append(linha_aparelho(d["nome"], "Por perto", [b], self.compacto))
            if not e.get("pareados") and not e.get("proximos"):
                v = Gtk.Label(label="Nenhum aparelho. Toque em Procurar ou deixe a caixinha visível.",
                              wrap=True, xalign=0)
                v.add_css_class("vazio")
                self.aparelhos.append(v)
            self.bt_procurar.set_label("Procurando…" if e.get("procurando") else "Procurar")
            self.bt_procurar.set_sensitive(not e.get("procurando"))
        v = e.get("visivel")
        self.sw_visivel.set_sensitive(v is not None)
        if v is not None and self.sw_visivel.get_active() != bool(v):
            self.app._sincronizando = True
            self.sw_visivel.set_active(bool(v))
            self.app._sincronizando = False

    def mostra_player(self, m):
        if self._mudou("players", [m.players, m.escolhido, m.atual()]):
            limpa(self.chips)
            if not m.players:
                v = Gtk.Label(label="Nenhum reprodutor aberto")
                v.add_css_class("vazio")
                self.chips.append(v)
            for nome, p in m.players.items():
                b = Gtk.Button(label=p["nome"])
                b.add_css_class("chip")
                if nome == m.atual():
                    b.add_css_class("ativo")
                b.connect("clicked", lambda _b, n=nome: self.app.escolher_player(n))
                self.chips.append(b)
        atual = m.players.get(m.atual() or "", {})
        self.faixa.set_text(atual.get("titulo") or ("Nada tocando" if not atual else atual.get("nome", "")))
        self.artista.set_text(atual.get("artista", ""))
        self.bt_play.get_child().set_text("pause" if atual.get("estado") == "Playing" else "play_arrow")
        url = atual.get("capa", "")
        if url != self.capa_url:
            self.capa_url = url
            carrega_capa(self.capa, url, lambda: self.capa_url == url)


def carrega_capa(pic, url, ainda_vale):
    if not url:
        pic.set_paintable(None)
        return
    if url.startswith("file://"):
        pic.set_filename(url[7:])
        return

    def _baixa():
        try:
            with urllib.request.urlopen(url, timeout=6) as r:
                dados = r.read()
            tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(dados))
            GLib.idle_add(lambda: (ainda_vale() and pic.set_paintable(tex), False)[1])
        except Exception:
            pass
    threading.Thread(target=_baixa, daemon=True).start()


# ---------------------------------------------------------------- app
class Central(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="br.casa.multimidia", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.estado = {}
        self.assinaturas = {}
        self.capa_url = None

    # -------------------------------------------------- montagem
    def do_activate(self):
        css = Gtk.CssProvider()
        css.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.win = Gtk.ApplicationWindow(application=self, title="Caixinha")
        self.win.add_css_class("principal")
        self.win.set_default_size(1280, 656)

        self.paineis = []  # pagina Bluetooth + painel flutuante
        self.overlay = Gtk.Overlay()
        self.pilha = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        self.pilha.add_named(self._pagina_inicio(), "inicio")
        self.pilha.add_named(self._pagina_bluetooth(), "bluetooth")
        self.pilha.add_named(self._pagina_ajustes(), "ajustes")
        self.overlay.set_child(self.pilha)
        self.pin_box = self._janela_pin()
        self.overlay.add_overlay(self.pin_box)
        self.win.set_child(self.overlay)
        self.win.present()

        self._barra()
        self._flutuante()
        acao = Gio.SimpleAction.new("abrir", GLib.VariantType.new("s"))  # gdbus: org.gtk.Actions.Activate
        acao.connect("activate", lambda _a, v: {"inicio": self.inicio, "painel": self.alternar_flutuante}.get(
            v.get_string(), lambda: self.abrir(v.get_string()))())
        self.add_action(acao)
        self.mpris = Mpris(lambda: GLib.idle_add(self._mostra_player))
        self._mostra_player()
        GLib.timeout_add_seconds(1, self._tique)
        self._tique()
        threading.Thread(target=self._sonda, daemon=True).start()

    # -------------------------------------------------- inicio
    def _pagina_inicio(self):
        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        topo = Gtk.Box(spacing=22, margin_top=22, margin_end=34, halign=Gtk.Align.END)
        self.topo_bt = icone("bluetooth_disabled", "topo-icone")
        self.topo_wifi = icone("speaker", "topo-icone")
        topo.append(self.topo_bt)
        topo.append(self.topo_wifi)
        raiz.append(topo)

        meio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, valign=Gtk.Align.START,
                       halign=Gtk.Align.START, margin_start=80, margin_top=6)
        self.relogio = Gtk.Label(xalign=0)
        self.relogio.add_css_class("relogio")
        self.data = Gtk.Label(xalign=0, margin_start=10)
        self.data.add_css_class("data")
        meio.append(self.relogio)
        meio.append(self.data)
        raiz.append(meio)

        linha = Gtk.Box(spacing=40, halign=Gtk.Align.CENTER, margin_bottom=46)
        for ident, nome, ic, cor, _cmd, _aid in APPS:
            if _cmd and not shutil.which(_cmd[0]):
                continue  # app nao instalado: sem atalho
            b = Gtk.Button()
            b.add_css_class("tile")
            caixa = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            circ = Gtk.Box(halign=Gtk.Align.CENTER)
            circ.add_css_class("circulo")
            prov = Gtk.CssProvider()
            prov.load_from_data(f"box {{ background: {cor}; }}".encode())
            circ.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            img = None  # glifos brancos em todos (estilo central de carro)
            if img:
                pic = Gtk.Picture.new_for_filename(img)
                pic.set_size_request(70, 70)
                pic.set_can_shrink(True)
                pic.set_halign(Gtk.Align.CENTER)
                pic.set_valign(Gtk.Align.CENTER)
                pic.set_hexpand(True)
                circ.append(pic)
            else:
                l = icone(ic)
                l.set_hexpand(True)
                circ.append(l)
            caixa.append(circ)
            n = Gtk.Label(label=nome)
            n.add_css_class("tile-nome")
            caixa.append(n)
            b.set_child(caixa)
            b.connect("clicked", lambda _b, i=ident: self.abrir(i))
            linha.append(b)
        raiz.append(linha)
        return raiz

    # -------------------------------------------------- bluetooth / reprodutor
    def _pagina_bluetooth(self):
        self.painel_pagina = PainelAudio(self, compacto=False)
        self.paineis.append(self.painel_pagina)
        return self.painel_pagina.raiz

    def _linha_aparelho(self, nome, sub, botoes):
        return linha_aparelho(nome, sub, botoes)

    # -------------------------------------------------- painel flutuante (estilo ChromeOS)
    def _flutuante(self):
        self.flut = Gtk.Window(application=self, title="painel")
        self.flut.add_css_class("flutuante")
        if LS:
            LS.init_for_window(self.flut)
            LS.set_layer(self.flut, LS.Layer.OVERLAY)
            for borda in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(self.flut, borda, True)
            LS.set_exclusive_zone(self.flut, 0)  # fica acima dos apps, sem cobrir a barra
            LS.set_keyboard_mode(self.flut, LS.KeyboardMode.ON_DEMAND)
            LS.set_namespace(self.flut, "painel")
        sobre = Gtk.Overlay()
        fundo = Gtk.Box(hexpand=True, vexpand=True)  # toque fora do painel fecha
        clique = Gtk.GestureClick()
        clique.connect("released", lambda *_: self.fechar_flutuante())
        fundo.add_controller(clique)
        sobre.set_child(fundo)
        self.painel_flut = PainelAudio(self, compacto=True)
        self.paineis.append(self.painel_flut)
        sobre.add_overlay(self.painel_flut.raiz)
        self.flut.set_child(sobre)
        self.flut.set_visible(False)

    def alternar_flutuante(self):
        if self.flut.get_visible():
            self.fechar_flutuante()
        else:
            self.assinaturas.clear()  # redesenha com o estado atual
            self._aplica(self.estado or {})
            self._mostra_player()
            self.flut.present()

    def fechar_flutuante(self):
        self.flut.set_visible(False)

    def expandir(self):
        self.fechar_flutuante()
        self.abrir("bluetooth")

    # -------------------------------------------------- ajustes
    def _pagina_ajustes(self):
        raiz = Gtk.Box(spacing=22, margin_top=24, margin_bottom=20, margin_start=28, margin_end=28)
        esq = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, hexpand=True)
        esq.add_css_class("cartao")
        t = Gtk.Label(label="SOM", xalign=0)
        t.add_css_class("cartao-titulo")
        esq.append(t)

        def linha_sw(nome, sub, cmd):
            sw = Gtk.Switch(valign=Gtk.Align.CENTER)
            sw.connect("state-set", lambda _s, v: self._chave(cmd, v))
            esq.append(self._linha_aparelho(nome, sub, [sw]))
            return sw
        self.sw_mpd = linha_sw("Saída do MPD", "Som que o Home Assistant / Music Assistant manda pelo MPD", "mpd")
        self.linha_mpd = self.sw_mpd.get_parent()

        raiz.append(esq)

        dir_ = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, hexpand=True)
        dir_.add_css_class("cartao")
        t = Gtk.Label(label="SOBRE", xalign=0)
        t.add_css_class("cartao-titulo")
        dir_.append(t)
        self.info = Gtk.Label(xalign=0, yalign=0, wrap=True)
        self.info.add_css_class("linha-nome")
        dir_.append(self.info)
        raiz.append(dir_)
        return raiz

    def _chave(self, cmd, v):
        if not getattr(self, "_sincronizando", False):
            api_cmd(cmd, "ON" if v else "OFF")
        return False

    # -------------------------------------------------- janela de PIN
    def _janela_pin(self):
        fundo = Gtk.Box(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        fundo.add_css_class("fundo-pin")
        j = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, halign=Gtk.Align.CENTER,
                    valign=Gtk.Align.CENTER, hexpand=True)
        j.add_css_class("janela-pin")
        j.append(icone("bluetooth_searching", "topo-icone"))
        self.pin_txt = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=34)
        self.pin_txt.add_css_class("pin-texto")
        self.pin_cod = Gtk.Label()
        self.pin_cod.add_css_class("pin-codigo")
        j.append(self.pin_txt)
        j.append(self.pin_cod)
        self.pin_botoes = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER, margin_top=8)
        r = Gtk.Button(label="Recusar")
        r.add_css_class("botao")
        r.add_css_class("vermelho")
        r.connect("clicked", lambda *_: (api_cmd("pin", "recusar"), self.pin_box.set_visible(False)))
        a = Gtk.Button(label="Parear")
        a.add_css_class("botao")
        a.add_css_class("azul")
        a.connect("clicked", lambda *_: (api_cmd("pin", "aceitar"), self.pin_box.set_visible(False)))
        self.pin_botoes.append(r)
        self.pin_botoes.append(a)
        j.append(self.pin_botoes)
        fundo.append(j)
        fundo.set_visible(False)
        return fundo

    # -------------------------------------------------- barra de baixo
    def _barra(self):
        self.barra = Gtk.Window(application=self, title="barra")
        self.barra.add_css_class("barra")
        if LS:
            LS.init_for_window(self.barra)
            LS.set_layer(self.barra, LS.Layer.TOP)
            for borda in (LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(self.barra, borda, True)
            LS.auto_exclusive_zone_enable(self.barra)
            LS.set_namespace(self.barra, "barra")
        caixa = Gtk.Box(spacing=14, margin_start=16, margin_end=16, margin_top=7, margin_bottom=7)
        caixa.add_css_class("barra")
        home = Gtk.Button(label="HOME")
        home.add_css_class("barra-home")
        home.connect("clicked", lambda *_: self.inicio())
        caixa.append(home)
        caixa.append(botao_redondo("volume_down", lambda: self._vol_passo(-5)))
        self.barra_vol = Gtk.Label(label="--%")
        self.barra_vol.add_css_class("barra-vol")
        caixa.append(self.barra_vol)
        caixa.append(botao_redondo("volume_up", lambda: self._vol_passo(5)))

        meio = Gtk.Box(spacing=10, hexpand=True, halign=Gtk.Align.CENTER)
        meio.append(botao_redondo("skip_previous", lambda: self.mpris.chama("Previous")))
        self.barra_play = botao_redondo("play_arrow", lambda: self.mpris.chama("PlayPause"))
        meio.append(self.barra_play)
        meio.append(botao_redondo("skip_next", lambda: self.mpris.chama("Next")))
        self.barra_faixa = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=40, margin_start=8)
        self.barra_faixa.add_css_class("barra-faixa")
        meio.append(self.barra_faixa)
        caixa.append(meio)

        self.barra_bt = botao_redondo("bluetooth", self.alternar_flutuante)
        caixa.append(self.barra_bt)
        caixa.append(botao_redondo("settings", lambda: self.abrir("ajustes")))
        self.barra_relogio = Gtk.Label(margin_start=6)
        self.barra_relogio.add_css_class("barra-relogio")
        caixa.append(self.barra_relogio)
        self.barra.set_child(caixa)
        self.barra.present()

    # -------------------------------------------------- navegacao
    def inicio(self):
        self.fechar_flutuante()
        self.pilha.set_visible_child_name("inicio")
        sway("workspace", "inicio")

    def abrir(self, ident):
        self.fechar_flutuante()
        if ident in ("bluetooth", "ajustes"):
            self.pilha.set_visible_child_name(ident)
            sway("workspace", "inicio")
            return
        app = next(a for a in APPS if a[0] == ident)
        cmd, aid = app[4], app[5]
        if not cmd or not shutil.which(cmd[0]):
            self._aviso(f"{app[1]} ainda não está instalado")
            return
        sway("workspace", ident)
        abertos = app_ids_abertos()
        if not any(aid.lower() in a.lower() for a in abertos):
            log = open(f"/var/log/caixinha/app-{ident}.log", "ab")
            env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}  # gtk4-layer-shell e so da barra
            subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True, env=env)

    def _aviso(self, texto, segundos=2):
        self.pin_txt.set_text(texto)
        self.pin_cod.set_text("")
        self.pin_botoes.set_visible(False)
        self.pin_box.set_visible(True)
        GLib.timeout_add_seconds(segundos, lambda: (self.pin_box.set_visible(False), self.pin_botoes.set_visible(True), False)[2])

    # -------------------------------------------------- volume
    def _vol_passo(self, d):
        v = (self.estado.get("volume") or 0) + d
        v = max(0, min(100, v))
        self.estado["volume"] = v
        self.barra_vol.set_text(f"{v}%")
        self._mexendo_vol = True
        for pa in self.paineis:
            pa.vol.set_value(v)
        self._mexendo_vol = False
        api_cmd("volume", v)

    def _vol_mexeu(self, scale):
        if getattr(self, "_mexendo_vol", False):
            return
        v = int(scale.get_value())
        self.estado["volume"] = v
        self.barra_vol.set_text(f"{v}%")
        self._mexendo_vol = True
        for pa in self.paineis:
            if pa.vol is not scale:
                pa.vol.set_value(v)
        self._mexendo_vol = False
        if getattr(self, "_vol_timer", None):
            GLib.source_remove(self._vol_timer)
        self._vol_timer = GLib.timeout_add(150, lambda: (api_cmd("volume", v), setattr(self, "_vol_timer", None), False)[2])

    # -------------------------------------------------- atualizacoes
    def _tique(self):
        agora = time.localtime()
        self.relogio.set_text(time.strftime("%H:%M", agora))
        self.data.set_text(f"{DIAS[agora.tm_wday]}, {agora.tm_mday} de {MESES[agora.tm_mon - 1]}")
        self.barra_relogio.set_text(time.strftime("%H:%M", agora))
        return True

    def _sonda(self):
        while True:
            e = api_get()
            if e is not None:
                GLib.idle_add(self._aplica, e)
            time.sleep(1.2)

    def _mudou(self, chave, valor):
        s = json.dumps(valor, sort_keys=True)
        if self.assinaturas.get(chave) == s:
            return False
        self.assinaturas[chave] = s
        return True

    def _aplica(self, e):
        self.estado = e
        if e.get("volume") is not None and self._mudou("vol", e["volume"]) and not getattr(self, "_vol_timer", None):
            self.barra_vol.set_text(f"{e['volume']}%")
        if self._mudou("bt", e.get("conectado")):
            ligado = bool(e.get("conectado"))
            self.topo_bt.set_text("bluetooth_connected" if ligado else "bluetooth_disabled")
            (self.barra_bt.add_css_class if ligado else self.barra_bt.remove_css_class)("ativo")
        for pa in self.paineis:
            pa.aplica(e)
        self.linha_mpd.set_visible(e.get("mpd") is not None)  # sem MPD instalado: some
        for sw, chave in ((self.sw_mpd, "mpd"),):
            v = e.get(chave)
            sw.set_sensitive(v is not None)
            if v is not None and sw.get_active() != bool(v):
                self._sincronizando = True
                sw.set_active(bool(v))
                self._sincronizando = False
        # PIN
        if self._mudou("pin", e.get("pin")):
            p = e.get("pin")
            if p:
                self.pin_botoes.set_visible(p.get("tipo") == "confirmar")
                if p.get("codigo"):
                    self.pin_txt.set_text(f"Parear com “{p['aparelho']}”?\nConfira se o código é o mesmo no aparelho.")
                    self.pin_cod.set_text(p["codigo"])
                else:
                    self.pin_txt.set_text(f"“{p['aparelho']}” quer se conectar à caixinha.")
                    self.pin_cod.set_text("")
                self.pin_box.set_visible(True)
                sway("workspace", "inicio")
            else:
                self.pin_box.set_visible(False)
        # sobre
        self.info.set_text(
            f"Moonlight: {socket.gethostname()} ({ip_local()})\n"
            f"Home Assistant (MQTT): {'conectado' if e.get('mqtt') else 'não configurado / desconectado'}\n"
            f"Bluetooth: {e.get('bluetooth', '—')}\n"
            f"Saída atual: {next((s['nome'] for s in e.get('saidas', []) if s['id'] == e.get('saida_atual')), '—')}")
        return False

    def escolher_player(self, nome):
        self.mpris.escolhido = nome
        self._mostra_player()

    def _mostra_player(self):
        m = self.mpris
        for pa in self.paineis:
            pa.mostra_player(m)
        atual = m.players.get(m.atual() or "", {})
        self.barra_play.get_child().set_text("pause" if atual.get("estado") == "Playing" else "play_arrow")
        self.barra_faixa.set_text(" — ".join(x for x in (atual.get("titulo"), atual.get("artista")) if x))
        return False


if __name__ == "__main__":
    Central().run(None)
