#!/usr/bin/env python3
"""
caixinha-multimidia - servico de audio/Bluetooth da central (https://github.com/JeanMB2003/caixinha-multimidia)

Configuracao: /etc/caixinha/caixinha.env (NOME, NODE, SAIDA_LOCAL) e /etc/caixinha/mqtt.env
(MQTT_HOST vazio = sem Home Assistant). Com MQTT, publica via Discovery um dispositivo no HA:
  - select  "Saida de audio"          -> alto-falante local (USB) ou Bluetooth
  - number  "Volume"
  - button  "Procurar dispositivos"    -> busca aparelhos Bluetooth por ~15 s
  - select  "Parear / conectar"
  - button  "Desconectar Bluetooth"
  - select  "Esquecer dispositivo"
  - sensor  "Bluetooth"                -> aparelho conectado (ou "Desconectado")
  - switch  "Visivel para parear"      -> celular encontra a caixinha por 3 min
  - text    "PIN do Moonlight"         -> digite o PIN que o Moonlight mostra ao parear (vai para o Sunshine)
  - sensor  "Pareamento Moonlight"     -> pedido pendente / resultado do ultimo PIN
  - switch  "Saida do MPD"             -> liga/desliga o som do MPD (se o MPD estiver instalado)

A central multimidia (multimidia.py) usa a mesma logica pela API local
http://127.0.0.1:8095 (GET /estado, POST /comando {"comando": ..., "valor": ...}).

Pareamento Bluetooth: este servico registra um agente no bluez (DisplayYesNo). Quando um
aparelho pede confirmacao, o codigo aparece na tela; sem resposta em 45 s e recusado.
"""

import json
import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------- configuracao
def load_env(path):
    env = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

AMB = load_env("/etc/caixinha/ambiente")
os.environ.update(AMB)
CFG = load_env("/etc/caixinha/mqtt.env")
GERAL = load_env("/etc/caixinha/caixinha.env")
MQTT_HOST = CFG.get("MQTT_HOST", "")
MQTT_PORT = int(CFG.get("MQTT_PORT", "1883"))
MQTT_USER = CFG.get("MQTT_USER")
MQTT_PASS = CFG.get("MQTT_PASS")

NODE = re.sub(r"[^a-z0-9_]", "_", GERAL.get("NODE") or f"caixinha_{socket.gethostname()}".lower())
BASE = f"caixinha/{NODE}"
DISC = "homeassistant"
NADA = "—"
LOCAL_SINK = GERAL.get("SAIDA_LOCAL", "")  # vazio: primeira saida que nao e Bluetooth
SCAN_SECONDS = 15
REFRESH_SECONDS = 5
VISIVEL_SEGUNDOS = 180
PIN_TIMEOUT = 45
API_PORTA = 8095
SUNSHINE = "https://127.0.0.1:47990"
SUNSHINE_CREDS = "/etc/caixinha/sunshine.creds"
MPD = ("127.0.0.1", 6600)

DEVICE = {
    "identifiers": [NODE],
    "name": GERAL.get("NOME") or "Caixinha",
    "manufacturer": "caixinha-multimidia",
    "model": "Central multimidia (Sunshine + PipeWire + bluez)",
}

lock = threading.RLock()
state = {
    "sinks": {}, "nearby": {}, "paired": {}, "connected": None, "scanning": False,
    "visivel": False, "pin": None,          # pin: {"aparelho", "codigo", "tipo"}
    "mpd": None,
}


# ---------------------------------------------------------------- utilidades
def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return 124, str(e)


def btctl(*args, timeout=30):
    return run(["bluetoothctl", *args], timeout=timeout)


def parse_devices(text):
    devs = {}
    for line in text.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.+)", line.strip())
        if m:
            mac, name = m.group(1), m.group(2).strip()
            if name.replace("-", ":") == mac:  # sem nome amigavel
                continue
            devs[mac] = name
    return devs


def label(mac, name):
    return f"{name} ({mac})"


def mac_from_label(text):
    m = re.search(r"\(?([0-9A-F]{2}(?::[0-9A-F]{2}){5})\)?\s*$", (text or "").upper())
    return m.group(1) if m else None


# ---------------------------------------------------------------- audio (PipeWire)
def list_sinks():
    code, out = run(["pactl", "-f", "json", "list", "sinks"])
    sinks = {}
    if code == 0:
        try:
            for s in json.loads(out):
                if s["name"].startswith("sink-sunshine"):  # saidas virtuais do Sunshine: nao sao escolhas
                    continue
                sinks[s["name"]] = s.get("description") or s["name"]
        except ValueError:
            pass
    return sinks


def default_sink():
    code, out = run(["pactl", "get-default-sink"])
    return out.strip() if code == 0 else None


def get_volume():
    code, out = run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
    m = re.search(r"(\d+)%", out) if code == 0 else None
    return int(m.group(1)) if m else None


def set_volume(pct):
    pct = max(0, min(100, int(round(float(pct)))))
    run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"])


ESCOLHIDA = {"v": None}  # ultima saida escolhida (HA/tela/Bluetooth)


def saida_local():
    sinks = list_sinks()
    if LOCAL_SINK in sinks:
        return LOCAL_SINK
    return next((n for n in sinks if not n.startswith("bluez_output.")), None)


def set_output(sink_name):
    if not sink_name:
        return
    ESCOLHIDA["v"] = sink_name
    run(["pactl", "set-default-sink", sink_name])
    _, out = run(["pactl", "list", "short", "sink-inputs"])
    for line in out.splitlines():
        sid = line.split("\t")[0]
        if sid.isdigit():
            run(["pactl", "move-sink-input", sid, sink_name])


# ---------------------------------------------------------------- MPD (saida liga/desliga)
def mpd_cmd(cmd):
    try:
        with socket.create_connection(MPD, timeout=3) as s:
            f = s.makefile("rw", encoding="utf8", newline="\n")
            f.readline()  # OK MPD x.y
            f.write(cmd + "\n"); f.flush()
            linhas = []
            while True:
                l = f.readline()
                if not l or l.startswith("OK") or l.startswith("ACK"):
                    return linhas if l.startswith("OK") else None
                linhas.append(l.rstrip("\n"))
    except OSError:
        return None


def mpd_ativo():
    out = mpd_cmd("outputs")
    if out is None:
        return None
    for l in out:
        if l.startswith("outputenabled:"):
            return l.split(":", 1)[1].strip() == "1"
    return None


def mpd_definir(ligar):
    mpd_cmd("enableoutput 0" if ligar else "disableoutput 0")


# ---------------------------------------------------------------- Moonlight (pareamento no Sunshine)
moon = {"status": "—"}


def _sunshine(metodo, caminho, dados=None):
    import base64, ssl, urllib.request
    cred = load_env(SUNSHINE_CREDS)
    aut = base64.b64encode(f"{cred.get('SUNSHINE_USUARIO', '')}:{cred.get('SUNSHINE_SENHA', '')}".encode()).decode()
    req = urllib.request.Request(SUNSHINE + caminho, method=metodo,
                                 data=json.dumps(dados).encode() if dados is not None else None,
                                 headers={"Authorization": f"Basic {aut}", "Content-Type": "application/json"})
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE  # certificado proprio do Sunshine, so local
    with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
        return json.loads(r.read() or b"{}")


def moonlight_pendentes():
    try:
        return _sunshine("GET", "/api/pin").get("pairings", [])
    except Exception:
        return None


def moonlight_pin(pin):
    pin = re.sub(r"\D", "", str(pin))
    if len(pin) != 4:
        moon["status"] = "PIN inválido (são 4 números)"
        return
    pend = moonlight_pendentes()
    if pend is None:
        moon["status"] = "Sunshine indisponível"
    elif not pend:
        moon["status"] = "Nenhum pedido: inicie o pareamento no Moonlight primeiro"
    else:
        p = pend[-1]
        try:
            r = _sunshine("POST", "/api/pin", {"pin": pin, "name": f"Moonlight {p.get('address', '')}".strip(),
                                               "pairing_id": p["id"]})
            ok = bool(r.get("status"))
        except Exception:
            ok = False
        moon["status"] = f"Pareado: {p.get('address', '')}" if ok else "PIN recusado pelo Sunshine"
    print(f"[moonlight] {moon['status']}", flush=True)
    publish_state()


def texto_moonlight():
    pend = moonlight_pendentes()
    if pend:
        return f"Aguardando PIN ({pend[-1].get('address', '?')})"
    return moon["status"]


# ---------------------------------------------------------------- Bluetooth (bluetoothctl)
def refresh_bt():
    _, out_all = btctl("devices")
    _, out_paired = btctl("devices", "Paired")
    _, out_conn = btctl("devices", "Connected")
    with lock:
        state["nearby"] = parse_devices(out_all)
        state["paired"] = parse_devices(out_paired)
        conn = parse_devices(out_conn)
        state["connected"] = next(iter(conn.items()), None)


def scan():
    with lock:
        if state["scanning"]:
            return
        state["scanning"] = True
    publish_state()
    btctl("--timeout", str(SCAN_SECONDS), "scan", "on", timeout=SCAN_SECONDS + 10)
    with lock:
        state["scanning"] = False
    refresh_all()


def pair_connect(mac):
    btctl("trust", mac)
    ok, msg = agente.parear(mac) if agente.pronto else (False, "agente indisponivel")
    if not ok and "AlreadyExists" not in msg:
        print(f"[pareamento] {mac}: {msg[-200:]}", flush=True)
        # sem agente (ou falhou): tenta o caminho antigo
        code, out = btctl("pair", mac, timeout=40)
        if code != 0 and "AlreadyExists" not in out:
            print(f"[pareamento] {mac}: {out.strip()[-200:]}", flush=True)
    btctl("connect", mac, timeout=30)
    for _ in range(15):  # espera o PipeWire criar a saida Bluetooth e muda para ela
        time.sleep(1)
        bt = [n for n in list_sinks() if n.startswith("bluez_output.") and mac.replace(":", "_") in n]
        if bt:
            set_output(bt[0])
            break
    refresh_all()


def connect_only(mac):
    btctl("connect", mac, timeout=30)
    for _ in range(10):
        time.sleep(1)
        bt = [n for n in list_sinks() if n.startswith("bluez_output.") and mac.replace(":", "_") in n]
        if bt:
            set_output(bt[0])
            break
    refresh_all()


def disconnect():
    with lock:
        conn = state["connected"]
    if conn:
        btctl("disconnect", conn[0])
    set_output(saida_local())
    refresh_all()


def forget(mac):
    btctl("remove", mac)
    set_output(saida_local())
    refresh_all()


# ---------------------------------------------------------------- agente de pareamento (D-Bus)
class Agente:
    """Agente bluez (DisplayYesNo) num thread com loop GLib proprio."""
    CAMINHO = "/caixinha/agente"

    def __init__(self):
        self.pronto = False
        self.pendente = None      # (ok_cb, err_cb, timer_id)
        self.bus = None
        threading.Thread(target=self._loop, daemon=True).start()

    # -- loop
    def _loop(self):
        try:
            import dbus, dbus.service, dbus.mainloop.glib
            from gi.repository import GLib
        except ImportError as e:
            print(f"[agente] sem dbus/gi: {e}", flush=True)
            return
        self.dbus, self.GLib = dbus, GLib
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.ctx = GLib.MainContext.default()
        self.bus = dbus.SystemBus()
        agente = self

        class Obj(dbus.service.Object):
            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Release(self):
                agente.pronto = False

            @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
            def AuthorizeService(self, device, uuid):
                return

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
            def RequestPinCode(self, device):
                agente._mostra(device, "0000", "pin")
                return "0000"

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
            def RequestPasskey(self, device):
                return dbus.UInt32(0)

            @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
            def DisplayPasskey(self, device, passkey, entered):
                agente._mostra(device, f"{int(passkey):06d}", "exibir")

            @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
            def DisplayPinCode(self, device, pincode):
                agente._mostra(device, str(pincode), "exibir")

            @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="",
                                 async_callbacks=("ok", "err"))
            def RequestConfirmation(self, device, passkey, ok, err):
                agente._pede(device, f"{int(passkey):06d}", ok, err)

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="",
                                 async_callbacks=("ok", "err"))
            def RequestAuthorization(self, device, ok, err):
                agente._pede(device, "", ok, err)

            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Cancel(self):
                agente._limpa(cancelado=True)

        self.obj = Obj(self.bus, self.CAMINHO)
        self.bus.watch_name_owner("org.bluez", lambda dono: dono and GLib.timeout_add_seconds(2, self._registra))
        # volume do celular (AVRCP "volume absoluto") -> volume da caixinha
        self.bus.add_signal_receiver(self._transporte_mudou, signal_name="PropertiesChanged",
                                     dbus_interface="org.freedesktop.DBus.Properties",
                                     bus_name="org.bluez", path_keyword="caminho")
        GLib.MainLoop().run()

    # ---- volume sincronizado com o celular (celular tocando NA caixinha)
    _ultimo_vol_cel = {"v": None, "t": 0.0}

    def _transporte_mudou(self, iface, mudou, _inval, caminho=None):
        if iface != "org.bluez.MediaTransport1" or "Volume" not in mudou:
            return
        if not self._e_celular_tocando(caminho):
            return
        pct = round(int(mudou["Volume"]) * 100 / 127)
        u = self._ultimo_vol_cel
        if u["v"] == pct and time.time() - u["t"] < 2:  # eco do que nos mesmos mandamos
            return
        u["v"], u["t"] = pct, time.time()
        print(f"[agente] volume do celular: {pct}%", flush=True)
        in_thread(lambda: (set_volume(pct), publish_state()))

    def _e_celular_tocando(self, caminho):
        """Transporte de um aparelho que manda audio PARA a caixinha (no CT o transporte e do perfil local A2DP Sink, UUID 110b)."""
        try:
            props = self.dbus.Interface(self.bus.get_object("org.bluez", caminho), "org.freedesktop.DBus.Properties")
            return str(props.Get("org.bluez.MediaTransport1", "UUID")).lower().startswith("0000110b")
        except Exception:
            return False

    def volume_para_celular(self, pct):
        """Volume mudado na central/HA: a barra do celular acompanha."""
        def _faz():
            try:
                om = self.dbus.Interface(self.bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
                for path, ifs in om.GetManagedObjects().items():
                    t = ifs.get("org.bluez.MediaTransport1")
                    if t and str(t.get("UUID", "")).lower().startswith("0000110b") and "Volume" in t:
                        v = max(0, min(127, round(int(pct) * 127 / 100)))
                        self._ultimo_vol_cel.update(v=round(v * 100 / 127), t=time.time())
                        props = self.dbus.Interface(self.bus.get_object("org.bluez", path),
                                                    "org.freedesktop.DBus.Properties")
                        props.Set("org.bluez.MediaTransport1", "Volume", self.dbus.UInt16(v))
            except Exception as e:
                print(f"[agente] volume para o celular: {e}", flush=True)
            return False
        if self.bus:
            self.GLib.idle_add(_faz)

    def _registra(self):
        try:
            mgr = self.dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
            try:
                mgr.UnregisterAgent(self.CAMINHO)
            except Exception:
                pass
            mgr.RegisterAgent(self.CAMINHO, "DisplayYesNo")
            mgr.RequestDefaultAgent(self.CAMINHO)
            self.pronto = True
            print("[agente] registrado como agente padrao do bluez", flush=True)
        except Exception as e:
            self.pronto = False
            print(f"[agente] falhou ao registrar: {e}", flush=True)
        return False

    def _nome(self, device):
        try:
            props = self.dbus.Interface(self.bus.get_object("org.bluez", device), "org.freedesktop.DBus.Properties")
            return str(props.Get("org.bluez.Device1", "Alias"))
        except Exception:
            return str(device).rsplit("dev_", 1)[-1].replace("_", ":")

    def _mostra(self, device, codigo, tipo):
        with lock:
            state["pin"] = {"aparelho": self._nome(device), "codigo": codigo, "tipo": tipo}
        in_thread(publish_state)
        self.GLib.timeout_add_seconds(30, lambda: (self._limpa(), False)[1])

    def _pede(self, device, codigo, ok, err):
        self._limpa(cancelado=True)
        tid = self.GLib.timeout_add_seconds(PIN_TIMEOUT, lambda: (self.responder(False), False)[1])
        with lock:
            self.pendente = (ok, err, tid)
            state["pin"] = {"aparelho": self._nome(device), "codigo": codigo, "tipo": "confirmar"}
        print(f"[agente] confirmar pareamento: {state['pin']}", flush=True)
        in_thread(publish_state)

    def _limpa(self, cancelado=False):
        with lock:
            p, self.pendente = self.pendente, None
            state["pin"] = None
        if p and cancelado:
            try:
                self.GLib.source_remove(p[2])
            except Exception:
                pass
        in_thread(publish_state)

    def responder(self, aceitar):
        """Chamado pelo HA ou pela tela (qualquer thread)."""
        def _faz():
            with lock:
                p, self.pendente = self.pendente, None
                state["pin"] = None
            if p:
                try:
                    self.GLib.source_remove(p[2])
                except Exception:
                    pass
                if aceitar:
                    p[0]()
                else:
                    p[1](self.dbus.DBusException("Recusado na caixinha", name="org.bluez.Error.Rejected"))
                print(f"[agente] pareamento {'aceito' if aceitar else 'recusado'}", flush=True)
            in_thread(publish_state)
            return False
        if self.bus:
            self.GLib.idle_add(_faz)

    def parear(self, mac):
        """Pair() pela nossa conexao: assim os pedidos de PIN chegam neste agente."""
        resultado = {}
        feito = threading.Event()

        def _faz():
            caminho = None
            try:
                om = self.dbus.Interface(self.bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
                for path, ifs in om.GetManagedObjects().items():
                    d = ifs.get("org.bluez.Device1")
                    if d and str(d.get("Address", "")).upper() == mac:
                        caminho = path
                        break
                if not caminho:
                    resultado["r"] = (False, "aparelho nao encontrado (procure de novo)")
                    feito.set(); return False
                dev = self.dbus.Interface(self.bus.get_object("org.bluez", caminho), "org.bluez.Device1")
                dev.Pair(reply_handler=lambda: (resultado.update(r=(True, "")), feito.set()),
                         error_handler=lambda e: (resultado.update(r=(False, str(e))), feito.set()),
                         timeout=60)
            except Exception as e:
                resultado["r"] = (False, str(e)); feito.set()
            return False

        self.GLib.idle_add(_faz)
        feito.wait(70)
        return resultado.get("r", (False, "tempo esgotado"))

    def visivel(self, ligar):
        def _faz():
            try:
                props = self.dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez/hci0"),
                                            "org.freedesktop.DBus.Properties")
                props.Set("org.bluez.Adapter1", "Pairable", self.dbus.Boolean(True))
                props.Set("org.bluez.Adapter1", "DiscoverableTimeout", self.dbus.UInt32(VISIVEL_SEGUNDOS))
                props.Set("org.bluez.Adapter1", "Discoverable", self.dbus.Boolean(bool(ligar)))
            except Exception as e:
                print(f"[agente] visivel: {e}", flush=True)
            return False
        if self.bus:
            self.GLib.idle_add(_faz)

    def esta_visivel(self):
        try:
            props = self.dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez/hci0"),
                                        "org.freedesktop.DBus.Properties")
            return bool(props.Get("org.bluez.Adapter1", "Discoverable"))
        except Exception:
            return False


agente = Agente()


# ---------------------------------------------------------------- MQTT / Home Assistant
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=NODE)
if MQTT_USER:
    client.username_pw_set(MQTT_USER, MQTT_PASS)
client.will_set(f"{BASE}/disponivel", "offline", retain=True)


def entity(component, key, name, extra):
    cfg = {
        "name": name,
        "unique_id": f"{NODE}_{key}",
        "object_id": f"caixinha_{key}",
        "device": DEVICE,
        "availability_topic": f"{BASE}/disponivel",
        **extra,
    }
    client.publish(f"{DISC}/{component}/{NODE}/{key}/config", json.dumps(cfg), retain=True)


def publish_discovery():
    with lock:
        sinks = dict(state["sinks"])
        nearby = dict(state["nearby"])
        paired = dict(state["paired"])
    entity("select", "saida", "Saída de áudio", {
        "icon": "mdi:speaker", "command_topic": f"{BASE}/saida/set", "state_topic": f"{BASE}/saida",
        "options": list(sinks.values()) or [NADA],
    })
    entity("number", "volume", "Volume", {
        "icon": "mdi:volume-high", "command_topic": f"{BASE}/volume/set", "state_topic": f"{BASE}/volume",
        "min": 0, "max": 100, "step": 1, "mode": "slider", "unit_of_measurement": "%",
    })
    entity("button", "procurar", "Procurar dispositivos Bluetooth", {
        "icon": "mdi:bluetooth-audio", "command_topic": f"{BASE}/procurar",
    })
    entity("select", "parear", "Parear / conectar", {
        "icon": "mdi:bluetooth-connect", "command_topic": f"{BASE}/parear/set", "state_topic": f"{BASE}/parear",
        "options": [NADA] + [label(m, n) for m, n in nearby.items()],
    })
    entity("button", "desconectar", "Desconectar Bluetooth", {
        "icon": "mdi:bluetooth-off", "command_topic": f"{BASE}/desconectar",
    })
    entity("select", "esquecer", "Esquecer dispositivo", {
        "icon": "mdi:delete", "command_topic": f"{BASE}/esquecer/set", "state_topic": f"{BASE}/esquecer",
        "options": [NADA] + [label(m, n) for m, n in paired.items()],
    })
    entity("sensor", "bluetooth", "Bluetooth", {"icon": "mdi:bluetooth", "state_topic": f"{BASE}/bluetooth"})
    entity("switch", "visivel", "Visível para parear", {
        "icon": "mdi:bluetooth-settings", "command_topic": f"{BASE}/visivel/set", "state_topic": f"{BASE}/visivel",
    })
    entity("text", "moonlight_pin", "PIN do Moonlight", {
        "icon": "mdi:dialpad", "command_topic": f"{BASE}/moonlight_pin/set", "state_topic": f"{BASE}/moonlight_pin",
        "min": 0, "max": 4, "pattern": "^[0-9]{0,4}$",
    })
    entity("sensor", "moonlight", "Pareamento Moonlight", {
        "icon": "mdi:monitor-share", "state_topic": f"{BASE}/moonlight",
    })
    # entidades de versoes antigas: remove do HA
    for comp, chave in (("sensor", "pin"), ("button", "pin_aceitar"), ("button", "pin_recusar"), ("text", "tocar")):
        client.publish(f"{DISC}/{comp}/{NODE}/{chave}/config", "", retain=True)
    if state["mpd"] is not None:  # so se o MPD estiver instalado
        entity("switch", "mpd", "Saída do MPD", {
            "icon": "mdi:music-box", "command_topic": f"{BASE}/mpd/set", "state_topic": f"{BASE}/mpd",
        })


def texto_bt():
    with lock:
        conn, scanning = state["connected"], state["scanning"]
    if scanning:
        return "Procurando…"
    return f"Conectado: {conn[1]}" if conn else "Desconectado"


def texto_pin():
    with lock:
        p = state["pin"]
    if not p:
        return NADA
    return f"{p['codigo']} – {p['aparelho']}" if p["codigo"] else f"Autorizar {p['aparelho']}?"


def publish_state():
    with lock:
        sinks = dict(state["sinks"])
        visivel, mpd = state["visivel"], state["mpd"]
    if not client.is_connected():
        return
    client.publish(f"{BASE}/saida", sinks.get(default_sink(), NADA), retain=True)
    vol = get_volume()
    if vol is not None:
        client.publish(f"{BASE}/volume", str(vol), retain=True)
    client.publish(f"{BASE}/bluetooth", texto_bt(), retain=True)
    client.publish(f"{BASE}/parear", NADA, retain=True)
    client.publish(f"{BASE}/esquecer", NADA, retain=True)
    client.publish(f"{BASE}/visivel", "ON" if visivel else "OFF", retain=True)
    client.publish(f"{BASE}/moonlight", texto_moonlight()[:255], retain=True)
    client.publish(f"{BASE}/moonlight_pin", "", retain=True)
    if mpd is not None:
        client.publish(f"{BASE}/mpd", "ON" if mpd else "OFF", retain=True)


_last_disc = {"v": None}


def refresh_all():
    refresh_bt()
    sinks = list_sinks()
    # O Sunshine (Moonlight/web) troca a saida padrao para a virtual dele ao transmitir; a caixinha e o
    # alto-falante da casa, entao o som volta para a saida escolhida (caixa USB ou Bluetooth).
    atual = default_sink() or ""
    if atual.startswith("sink-sunshine"):
        alvo = ESCOLHIDA["v"] if ESCOLHIDA["v"] in sinks else saida_local()
        if alvo in sinks:
            set_output(alvo)
    mpd = mpd_ativo()
    visivel = agente.esta_visivel() if agente.pronto else False
    with lock:
        state.update(sinks=sinks, mpd=mpd, visivel=visivel)
        sig = json.dumps([sorted(sinks.values()), sorted(state["nearby"]), sorted(state["paired"]), mpd is None])
    if client.is_connected() and sig != _last_disc["v"]:
        _last_disc["v"] = sig
        publish_discovery()
    publish_state()


def in_thread(fn, *a):
    threading.Thread(target=fn, args=a, daemon=True).start()


# ---------------------------------------------------------------- comandos (HA e tela)
def comando(nome, valor=""):
    valor = (valor or "").strip() if isinstance(valor, str) else valor
    print(f"[comando] {nome} = {valor}", flush=True)
    if nome == "saida":
        with lock:
            sinks = dict(state["sinks"])
        alvo = valor if valor in sinks else next((k for k, v in sinks.items() if v == valor), None)
        if alvo:
            set_output(alvo)
        in_thread(refresh_all)
    elif nome == "volume":
        try:
            set_volume(valor)
            agente.volume_para_celular(float(valor))
        except (TypeError, ValueError):
            pass
        in_thread(publish_state)
    elif nome == "procurar":
        in_thread(scan)
    elif nome == "parear":
        mac = mac_from_label(valor)
        if mac:
            in_thread(pair_connect, mac)
    elif nome == "conectar":
        mac = mac_from_label(valor)
        if mac:
            in_thread(connect_only, mac)
    elif nome == "desconectar":
        in_thread(disconnect)
    elif nome == "esquecer":
        mac = mac_from_label(valor)
        if mac:
            in_thread(forget, mac)
    elif nome == "visivel":
        agente.visivel(str(valor).upper() in ("ON", "1", "TRUE"))
        with lock:
            state["visivel"] = str(valor).upper() in ("ON", "1", "TRUE")
        in_thread(publish_state)
    elif nome == "pin":
        agente.responder(str(valor).lower() in ("aceitar", "on", "1", "true", "sim"))
    elif nome == "mpd":
        mpd_definir(str(valor).upper() in ("ON", "1", "TRUE"))
        in_thread(refresh_all)


TOPICOS = {
    "saida/set": "saida", "volume/set": "volume", "procurar": "procurar", "parear/set": "parear",
    "desconectar": "desconectar", "esquecer/set": "esquecer", "visivel/set": "visivel",
    "mpd/set": "mpd",
}


def on_message(_c, _u, msg):
    payload = msg.payload.decode("utf8", "ignore").strip()
    sub = msg.topic[len(BASE) + 1:]
    if sub == "moonlight_pin/set":
        if payload:
            in_thread(moonlight_pin, payload)
    elif sub in TOPICOS:
        comando(TOPICOS[sub], payload)


def on_connect(c, _u, _f, rc, _p=None):
    print(f"[mqtt] conectado ({rc})", flush=True)
    c.publish(f"{BASE}/disponivel", "online", retain=True)
    for t in list(TOPICOS) + ["moonlight_pin/set"]:
        c.subscribe(f"{BASE}/{t}")
    _last_disc["v"] = None
    in_thread(refresh_all)


client.on_connect = on_connect
client.on_message = on_message


# ---------------------------------------------------------------- API local (tela)
def estado_json():
    with lock:
        s = {
            "saidas": [{"id": k, "nome": v} for k, v in state["sinks"].items()],
            "proximos": [{"mac": m, "nome": n} for m, n in state["nearby"].items() if m not in state["paired"]],
            "pareados": [{"mac": m, "nome": n} for m, n in state["paired"].items()],
            "conectado": {"mac": state["connected"][0], "nome": state["connected"][1]} if state["connected"] else None,
            "procurando": state["scanning"], "visivel": state["visivel"], "pin": state["pin"],
            "mpd": state["mpd"],
        }
    s["saida_atual"] = default_sink()
    s["volume"] = get_volume()
    s["bluetooth"] = texto_bt()
    s["mqtt"] = client.is_connected()
    return s


class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/estado"):
            self._json(estado_json())
        else:
            self._json({"erro": "?"}, 404)

    def do_POST(self):
        if not self.path.startswith("/comando"):
            return self._json({"erro": "?"}, 404)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            d = json.loads(self.rfile.read(n) or b"{}")
            comando(d.get("comando", ""), d.get("valor", ""))
            self._json({"ok": True})
        except Exception as e:
            self._json({"erro": str(e)}, 400)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", API_PORTA), Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if MQTT_HOST and os.environ.get("CAIXINHA_MQTT", "1") != "0":  # sem MQTT_HOST: funciona so pela tela
        client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
        client.loop_start()
    while True:
        try:
            refresh_all()
        except Exception as e:  # nunca derrubar o servico por um erro pontual
            print(f"[erro] {e}", flush=True)
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
