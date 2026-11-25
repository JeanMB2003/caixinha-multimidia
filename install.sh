#!/usr/bin/env bash
# =====================================================================================
#  caixinha-multimidia - instalador
#
#  Central de som estilo multimidia de carro, SEM monitor: tela virtual vista pelo Moonlight
#  (Sunshine), Bluetooth (celular toca na caixinha, PIN na tela), radio, YT Music, MPD e
#  Home Assistant por MQTT (opcional).
#
#  Uso (no Proxmox cria um LXC; em Debian/Ubuntu instala na propria maquina):
#    bash -c "$(curl -fsSL https://raw.githubusercontent.com/JeanMB2003/caixinha-multimidia/main/install.sh)"
#
#  Outros modos:  ... install.sh --atualizar   (dentro da caixinha: baixa a versao nova dos programas)
#  Variaveis:     CAIXINHA_REPO=usuario/repo  CAIXINHA_REF=branch|tag  (de onde baixar)
# =====================================================================================
set -o pipefail
REPO=${CAIXINHA_REPO:-JeanMB2003/caixinha-multimidia}
REF=${CAIXINHA_REF:-main}
TARBALL=${CAIXINHA_TARBALL:-"https://codeload.github.com/$REPO/tar.gz/$REF"}  # ou um .tar.gz local (testes)
DEST=/opt/caixinha-multimidia
CONF=/etc/caixinha
TITULO="caixinha-multimidia"
LOG=/var/log/caixinha-instalar.log

C_B=$'\e[1;34m'; C_OK=$'\e[1;32m'; C_AV=$'\e[1;33m'; C_ER=$'\e[1;31m'; C_0=$'\e[0m'
msg()  { echo "${C_B}==>${C_0} $*"; echo "$(date '+%F %T') $*" >> $LOG; }
ok()   { echo "  ${C_OK}ok${C_0} $*"; }
aviso(){ echo "  ${C_AV}aviso:${C_0} $*"; echo "$(date '+%F %T') AVISO $*" >> $LOG; }
erro() { echo "${C_ER}ERRO:${C_0} $*" >&2; echo "$(date '+%F %T') ERRO $*" >> $LOG; exit 1; }
[ "$(id -u)" = 0 ] || erro "rode como root"
umask 022  # os servicos rodam como 'som': nada pode nascer legivel so pelo root (umask 077 herdado quebrava tudo)

# ------------------------------------------------------------------ dialogos (whiptail)
WT=$(command -v whiptail || true)
# Cancelar num dialogo: os dialogos rodam dentro de $(...), entao um 'exit' sairia so do subshell
trap 'echo; echo "${C_AV}cancelado${C_0}"; exit 1' USR1
cancela() { kill -USR1 $$; exit 1; }
AUTO=${CAIXINHA_AUTO:-0}   # CAIXINHA_AUTO=1: sem perguntas, tudo no padrao recomendado (sem MQTT)
menu() { # menu "pergunta" tag desc ...
  [ "$AUTO" = 1 ] && { echo "$2"; return 0; }
  local q=$1; shift
  if [ -n "$WT" ]; then whiptail --title "$TITULO" --menu "$q" 20 78 10 "$@" 3>&1 1>&2 2>&3 || cancela
  else echo "$q" >&2; local i=0 t; for t in "$@"; do i=$((i+1)); [ $((i%2)) = 1 ] && echo "  $t" >&2; done; read -rp "> " t; echo "$t"; fi
}
entrada() { # entrada "pergunta" "padrao"
  [ "$AUTO" = 1 ] && { echo "$2"; return 0; }
  if [ -n "$WT" ]; then whiptail --title "$TITULO" --inputbox "$1" 11 78 "$2" 3>&1 1>&2 2>&3 || cancela
  else read -rp "$1 [$2]: " r; echo "${r:-$2}"; fi
}
senha() { # senha "pergunta"
  [ "$AUTO" = 1 ] && { echo ""; return 0; }
  if [ -n "$WT" ]; then whiptail --title "$TITULO" --passwordbox "$1" 10 78 3>&1 1>&2 2>&3 || cancela
  else read -rsp "$1: " r; echo >&2; echo "$r"; fi
}
simnao() { # simnao "pergunta" [s|n]
  [ "$AUTO" = 1 ] && { [ "${2:-s}" = s ]; return; }
  if [ -n "$WT" ]; then
    if [ "${2:-s}" = n ]; then whiptail --title "$TITULO" --defaultno --yesno "$1" 12 78; else whiptail --title "$TITULO" --yesno "$1" 12 78; fi
  else read -rp "$1 [${2:-s}]: " r; r=${r:-${2:-s}}; [[ $r == [sSyY]* ]]; fi
}
aleatorio() { head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c1-${1:-16}; }

baixa_fontes() { # baixa o repositorio inteiro (programas + arquivos de sistema) para $1
  local alvo=$1
  command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null; }
  rm -rf "$alvo.novo"; mkdir -p "$alvo.novo"
  if [ -f "$TARBALL" ]; then tar xzf "$TARBALL" -C "$alvo.novo" --strip-components=1 || erro "pacote $TARBALL invalido"
  else curl -fsSL "$TARBALL" | tar xz -C "$alvo.novo" --strip-components=1 || erro "nao consegui baixar $TARBALL"; fi
  rm -rf "$alvo"; mv "$alvo.novo" "$alvo"
}

# ------------------------------------------------------------------ perguntas comuns
perguntas_comuns() { # usa/preenche: AVANCADO NOME RES RADIO YTM MPD BT MQTT_* SUN_*
  NOME=${NOME:-Caixinha}; RES=${RES:-1280x720}; RADIO=${RADIO:-1}; YTM=${YTM:-1}; MPD=${MPD:-1}
  SUN_USER=${SUN_USER:-caixinha}; SUN_PASS=${SUN_PASS:-$(aleatorio 16)}
  if [ "$AVANCADO" = 1 ]; then
    NOME=$(entrada "Nome da caixinha (aparece no Moonlight e no Home Assistant)" "$NOME" | tr -cd 'A-Za-z0-9 _-')
    NOME=${NOME:-Caixinha}
    RES=$(menu "Resolucao da tela virtual" 1280x720 "720p - recomendado, leve" 1920x1080 "1080p - mais nitido" 1024x600 "estilo central de carro 7 polegadas")
    RADIO=0; YTM=0; MPD=0
    simnao "Instalar o Radio (Shortwave, milhares de radios online)?" s && RADIO=1
    if [ "$(dpkg --print-architecture 2>/dev/null || echo amd64)" = amd64 ] || [ "$MODO" = proxmox ]; then
      simnao "Instalar o YT Music (app YouTube Music, ~400 MB de RAM aberto)?" s && YTM=1
    fi
    simnao "Instalar o MPD (o Home Assistant / Music Assistant toca musica por ele)?" s && MPD=1
    SUN_USER=$(entrada "Usuario do painel do Sunshine (https://IP:47990)" "$SUN_USER")
    local s; s=$(senha "Senha do painel do Sunshine (vazio = gerar uma aleatoria)"); [ -n "$s" ] && SUN_PASS=$s
  fi
  if simnao "Conectar ao Home Assistant pelo MQTT?\n\n(O HA cria sozinho o dispositivo com saida de audio, volume,\nBluetooth e o campo 'PIN do Moonlight'. Pode deixar para depois:\nbasta editar $CONF/mqtt.env)" n; then
    local achado=""
    command -v avahi-browse >/dev/null && achado=$(timeout 4 avahi-browse -rpt _mqtt._tcp 2>/dev/null | awk -F';' '$1=="=" && $3=="IPv4" {print $8; exit}')
    MQTT_HOST=$(entrada "Endereco do broker MQTT (no HA: o app Mosquitto)" "${MQTT_HOST:-$achado}")
    MQTT_PORT=$(entrada "Porta do MQTT" "${MQTT_PORT:-1883}")
    MQTT_USER=$(entrada "Usuario do MQTT (no Mosquitto do HA pode ser um usuario do proprio HA)" "${MQTT_USER:-}")
    MQTT_PASS=$(senha "Senha do MQTT")
  fi
}

salva_respostas() { # grava as respostas para o modo --alvo (dentro do CT ou nesta maquina)
  mkdir -p "$(dirname "$1")"; umask 077
  cat > "$1" <<EOF
NOME='${NOME//\'/}'
RES='$RES'
RADIO='$RADIO'
YTM='$YTM'
MPD='$MPD'
BT='$BT'
EM_LXC='${EM_LXC:-0}'
SOM_HW='${SOM_HW:-}'
SOM_DESC='${SOM_DESC:-}'
MQTT_HOST='${MQTT_HOST:-}'
MQTT_PORT='${MQTT_PORT:-1883}'
MQTT_USER='${MQTT_USER:-}'
MQTT_PASS='${MQTT_PASS//\'/}'
SUN_USER='$SUN_USER'
SUN_PASS='${SUN_PASS//\'/}'
EOF
  umask 022  # o umask 077 vazava para o pct exec e deixava tudo dentro do CT legivel so pelo root
}

# =====================================================================================
#  MODO PROXMOX: cria o LXC e prepara o host
# =====================================================================================
instala_proxmox() {
  MODO=proxmox; EM_LXC=1
  local escolha
  escolha=$(menu "Bem-vindo! Isto cria um container (LXC) com a central multimidia.\n\nComo instalar?" \
    padrao "Padrao (recomendado) - perguntas minimas" avancado "Avancado - escolher tudo")
  [ "$escolha" = avancado ] && AVANCADO=1 || AVANCADO=0

  # ---- valores padrao detectados
  CTID=$(pvesh get /cluster/nextid); HOSTN=caixinha; RAM=2048; CORES=4; DISCO=16; BRIDGE=vmbr0; IPCONF=dhcp
  STORAGE=$(pvesm status --content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $6, $1}' | sort -rn | head -1 | awk '{print $2}')
  TSTORE=$(pvesm status --content vztmpl 2>/dev/null | awk 'NR>1 && $3=="active" {print $1; exit}')
  [ -n "$STORAGE" ] && [ -n "$TSTORE" ] || erro "nao achei armazenamento para o container/template"

  # GPU (render) para codificar o video
  declare -a GOPT=(); local g pci
  for g in /dev/dri/by-path/pci-*-render; do
    [ -e "$g" ] || continue; pci=$(basename "$g" | sed 's/^pci-//; s/-render$//')
    GOPT+=("$(readlink -f "$g")" "$(lspci -s "$pci" | sed -E 's/^[^:]+:[^:]+: //' | cut -c1-55)")
  done
  GPU=""; for ((i=0; i<${#GOPT[@]}; i+=2)); do case "${GOPT[i+1]}" in *NVIDIA*) ;; *) GPU=${GOPT[i]}; break;; esac; done

  # placa de som (saida local)
  declare -a AOPT=(); local n id desc
  # so placas com saida (pcm0p): controle remoto/microfone USB nao servem de alto-falante
  while read -r n id desc; do [ -e /proc/asound/card$n/pcm0p ] && AOPT+=("$n" "$id - $desc"); done < <(awk -F'[][]' '/^ *[0-9]+ \[/ {gsub(/ /,"",$1); gsub(/ +$/,"",$2); split($3,a," - "); print $1, $2, a[2]}' /proc/asound/cards 2>/dev/null)
  # padrao: placa USB com saida; sem ela, nenhuma (HDMI da GPU raramente e o alto-falante; escolha no Avancado)
  SOM=""; for f in /proc/asound/card*/usbid; do
    n=$(echo "$f" | sed -E 's#.*/card([0-9]+)/.*#\1#')
    [ -e /proc/asound/card$n/pcm0p ] && { SOM=$n; break; }
  done

  BT=0; ls /sys/class/bluetooth/hci* >/dev/null 2>&1 && BT=1

  if [ "$AVANCADO" = 1 ]; then
    CTID=$(entrada "ID do container" "$CTID")
    HOSTN=$(entrada "Nome (hostname) do container" "$HOSTN")
    RAM=$(entrada "Memoria RAM (MiB) - minimo 1536, recomendado 2048" "$RAM")
    CORES=$(entrada "Nucleos de CPU" "$CORES")
    DISCO=$(entrada "Disco (GiB)" "$DISCO")
    declare -a SOPT=(); while read -r nm tipo; do SOPT+=("$nm" "$tipo"); done < <(pvesm status --content rootdir | awk 'NR>1 && $3=="active" {printf "%s %s-livre-%dGiB\n", $1, $2, $6/1048576}')
    STORAGE=$(menu "Armazenamento do disco" "${SOPT[@]}")
    BRIDGE=$(entrada "Bridge de rede" "$BRIDGE")
    IPCONF=$(entrada "IP do container: 'dhcp' ou IP/mascara,gateway (ex.: 192.168.1.50/24,192.168.1.1)" "$IPCONF")
    if [ ${#GOPT[@]} -gt 0 ]; then
      GPU=$(menu "GPU que vai codificar o video do Moonlight (Intel/AMD; NVIDIA aqui = sem aceleracao)" "${GOPT[@]}" nenhuma "Sem GPU (codifica na CPU)")
      [ "$GPU" = nenhuma ] && GPU=""
    fi
    if [ ${#AOPT[@]} -gt 0 ]; then
      SOM=$(menu "Placa de som da caixinha (saida local)" "${AOPT[@]}" nenhuma "Sem saida local (so Bluetooth)")
      [ "$SOM" = nenhuma ] && SOM=""
    fi
    if ls /sys/class/bluetooth/hci* >/dev/null 2>&1; then simnao "Usar o Bluetooth do host (celular toca na caixinha, caixas BT)?" s && BT=1 || BT=0; fi
  fi
  pct status "$CTID" >/dev/null 2>&1 && erro "o ID $CTID ja existe"
  perguntas_comuns
  if [ -n "$SOM" ]; then SOM_HW="hw:$SOM,0"; SOM_DESC="$(awk -v n="$SOM" '$1==n {sub(/^[^]]*\]: *[^ ]* - /,""); print; exit}' /proc/asound/cards | tr -cd 'A-Za-z0-9 ()._-' | cut -c1-40)"; fi

  local resumo="Container $CTID '$HOSTN': ${RAM} MB, ${CORES} CPU, ${DISCO} GB em $STORAGE, rede $BRIDGE ($IPCONF)
GPU: ${GPU:-nenhuma (video codificado na CPU)}
Som: ${SOM:+placa $SOM }${SOM:-nenhuma saida local}   Bluetooth: $([ "$BT" = 1 ] && echo sim || echo nao)
Tela: $RES   Apps: $([ "$RADIO" = 1 ] && echo -n "Radio ")$([ "$YTM" = 1 ] && echo -n "YT-Music ")$([ "$MPD" = 1 ] && echo -n "MPD")
Home Assistant (MQTT): ${MQTT_HOST:-nao}"
  simnao "Confirmar?\n\n$resumo" s || erro "cancelado"

  msg "Baixando os arquivos ($REPO@$REF)"
  baixa_fontes /tmp/caixinha-fontes; ok

  # ---- template
  msg "Template Debian 13"
  local TPL; TPL=$(pveam list "$TSTORE" 2>/dev/null | awk '{print $1}' | grep -E 'debian-13-standard_.*_amd64' | sort -V | tail -1)
  if [ -z "$TPL" ]; then
    pveam update >/dev/null 2>&1
    local TN; TN=$(pveam available --section system | awk '{print $2}' | grep -E 'debian-13-standard_.*_amd64' | sort -V | tail -1)
    [ -n "$TN" ] || erro "template debian-13 nao encontrado"
    pveam download "$TSTORE" "$TN" >/dev/null || erro "falha ao baixar o template"
    TPL="$TSTORE:vztmpl/$TN"
  fi; ok "$TPL"

  # ---- container
  msg "Criando o container $CTID"
  local NET="name=eth0,bridge=$BRIDGE"
  if [ "$IPCONF" = dhcp ]; then NET="$NET,ip=dhcp"; else NET="$NET,ip=${IPCONF%%,*},gw=${IPCONF##*,}"; fi
  pct create "$CTID" "$TPL" --hostname "$HOSTN" --memory "$RAM" --swap 512 --cores "$CORES" \
    --rootfs "$STORAGE:$DISCO" --net0 "$NET" --unprivileged 1 --features nesting=1 \
    --onboot 1 --tags caixinha --ostype debian >/dev/null || erro "pct create falhou"
  pct start "$CTID" || erro "o container nao ligou"
  for _ in $(seq 30); do pct exec "$CTID" -- sh -c 'ip -4 route | grep -q default' 2>/dev/null && break; sleep 1; done
  pct exec "$CTID" -- sh -c 'groupadd -f -r render; groupadd -f -r input' >/dev/null 2>&1
  local GR GI; GR=$(pct exec "$CTID" -- getent group render | cut -d: -f3); GI=$(pct exec "$CTID" -- getent group input | cut -d: -f3)
  pct stop "$CTID"; ok "grupos no CT: render=$GR input=$GI"

  msg "Dispositivos do container (GPU, som, teclado/mouse/toque do Moonlight)"
  modprobe uinput 2>/dev/null; modprobe uhid 2>/dev/null
  grep -qx uinput /etc/modules-load.d/caixinha.conf 2>/dev/null || printf 'uinput\nuhid\n' > /etc/modules-load.d/caixinha.conf
  local d=0 C=/etc/pve/lxc/$CTID.conf
  pct set "$CTID" --dev$d "/dev/uinput,gid=$GI,mode=0660" >/dev/null; d=$((d+1))
  pct set "$CTID" --dev$d "/dev/uhid,gid=$GI,mode=0660" >/dev/null; d=$((d+1))
  [ -n "$GPU" ] && { pct set "$CTID" --dev$d "$GPU,gid=$GR,mode=0660" >/dev/null; d=$((d+1)); }
  if [ -n "$SOM" ]; then
    pct set "$CTID" --dev$d "/dev/snd/controlC$SOM,gid=29,mode=0660" >/dev/null; d=$((d+1))
    pct set "$CTID" --dev$d "/dev/snd/pcmC${SOM}D0p,gid=29,mode=0660" >/dev/null; d=$((d+1))
  fi
  {
    echo "lxc.cgroup2.devices.allow: c 13:* rwm"
    echo "lxc.mount.entry: /run/multimidia/input dev/input none bind,optional,create=dir 0 0"
    echo "lxc.mount.entry: /run/udev mnt/hostudev none bind,ro,optional,create=dir 0 0"
    if [ "$BT" = 1 ]; then
      # o servico da caixinha (usuario 2000) fala com o bluez do host pelo D-Bus: mesmo uid dos dois lados
      echo "lxc.mount.entry: /run/dbus mnt/dbus-host none bind,create=dir 0 0"
      echo "lxc.apparmor.raw: dbus,"
      echo "lxc.idmap: u 0 100000 2000"
      echo "lxc.idmap: u 2000 2000 1"
      echo "lxc.idmap: u 2001 102001 63535"
      echo "lxc.idmap: g 0 100000 65536"
    fi
  } >> "$C"
  if [ "$BT" = 1 ]; then
    grep -qx 'root:2000:1' /etc/subuid || echo 'root:2000:1' >> /etc/subuid
    command -v bluetoothctl >/dev/null || { apt-get update -qq; apt-get install -y -qq bluez >/dev/null; }
    systemctl enable --now bluetooth >/dev/null 2>&1
  fi
  ok

  msg "Pecas no host (entrada virtual do Moonlight)"
  # caixinhas que ja existem neste host continuam recebendo a entrada (lista de CTs)
  local LISTA="" ANT="" DONO_ANT="" c
  if [ -r /etc/caixinha-multimidia.conf ]; then
    ANT=$(sed -n 's/^CT=//p' /etc/caixinha-multimidia.conf | tr -d "\"'")
    DONO_ANT=$(sed -n 's/^DONO=//p' /etc/caixinha-multimidia.conf | tr -d "\"'")
  elif [ -f /usr/local/bin/multimidia-input ]; then  # versao antiga, com o CT fixo no script
    ANT=$(sed -n 's/^CT=\${MULTIMIDIA_CT:-\([0-9]*\)}.*/\1/p' /usr/local/bin/multimidia-input)
  fi
  for c in $ANT; do [ "$c" != "$CTID" ] && [ -e /etc/pve/lxc/$c.conf ] && LISTA="$LISTA $c"; done
  LISTA="${LISTA# } $CTID"; LISTA=${LISTA# }
  [ -n "${LISTA% *}" ] && [ "$LISTA" != "$CTID" ] && aviso "este host ja tem caixinha(s) (CT ${LISTA% $CTID}); todas recebem o teclado/mouse/toque do Moonlight"
  [ -n "$DONO_ANT" ] && [ "$DONO_ANT" != "100000:$((100000 + GI))" ] && aviso "grupo input diferente entre as caixinhas ($DONO_ANT x 100000:$((100000 + GI)))"
  install -m 755 /tmp/caixinha-fontes/host/multimidia-input /usr/local/bin/multimidia-input
  install -m 644 /tmp/caixinha-fontes/host/99-multimidia-input.rules /etc/udev/rules.d/99-multimidia-input.rules
  install -m 644 /tmp/caixinha-fontes/host/multimidia-input.service /etc/systemd/system/multimidia-input.service
  printf 'CT="%s"\nDONO=100000:%s\n' "$LISTA" "$((100000 + GI))" > /etc/caixinha-multimidia.conf
  udevadm control --reload; systemctl daemon-reload; systemctl enable multimidia-input >/dev/null 2>&1
  systemctl restart multimidia-input; ok

  msg "Ligando o container e instalando a central (demora alguns minutos)"
  pct start "$CTID" || erro "o container nao ligou"
  for _ in $(seq 30); do pct exec "$CTID" -- sh -c 'ip -4 route | grep -q default' 2>/dev/null && break; sleep 1; done
  salva_respostas /tmp/caixinha-respostas.env
  pct exec "$CTID" -- mkdir -p "$CONF"
  pct push "$CTID" /tmp/caixinha-respostas.env "$CONF/instalar.env" --perms 600; rm -f /tmp/caixinha-respostas.env
  pct push "$CTID" /tmp/caixinha-fontes/install.sh /root/caixinha-instalar.sh --perms 755
  local TB_CT=""; [ -f "$TARBALL" ] && { pct push "$CTID" "$TARBALL" /root/caixinha-fontes.tar.gz; TB_CT=/root/caixinha-fontes.tar.gz; }
  pct exec "$CTID" -- env CAIXINHA_REPO="$REPO" CAIXINHA_REF="$REF" ${TB_CT:+CAIXINHA_TARBALL=$TB_CT} TERM=dumb bash /root/caixinha-instalar.sh --alvo \
    || erro "a instalacao dentro do container falhou (veja $LOG dentro do CT $CTID)"
  local IP; IP=$(pct exec "$CTID" -- sh -c "ip -4 -br a show eth0 | awk '{print \$3}' | cut -d/ -f1")
  rm -rf /tmp/caixinha-fontes
  final "$IP" "container $CTID"
}

# =====================================================================================
#  MODO ALVO: instala a central nesta maquina (dentro do CT ou num Debian/Ubuntu comum)
# =====================================================================================
instala_alvo() {
  umask 022
  local RESP=$CONF/instalar.env
  if [ -r "$RESP" ]; then . "$RESP"
  else
    MODO=local; EM_LXC=0
    command -v whiptail >/dev/null || { apt-get update -qq; apt-get install -y -qq whiptail >/dev/null 2>&1; WT=$(command -v whiptail || true); }
    grep -qE '^ID=(debian|ubuntu)' /etc/os-release || erro "so Debian 13 (recomendado) ou Ubuntu 24.04"
    local escolha
    escolha=$(menu "Bem-vindo! Isto instala a central multimidia NESTA maquina.\n\nComo instalar?" \
      padrao "Padrao (recomendado) - perguntas minimas" avancado "Avancado - escolher tudo")
    [ "$escolha" = avancado ] && AVANCADO=1 || AVANCADO=0
    BT=0; ls /sys/class/bluetooth/hci* >/dev/null 2>&1 && BT=1
    [ "$AVANCADO" = 1 ] && [ "$BT" = 1 ] && { simnao "Usar o Bluetooth desta maquina?" s || BT=0; }
    perguntas_comuns
    simnao "Instalar agora?\n\nTela $RES, Bluetooth $([ "$BT" = 1 ] && echo sim || echo nao), MQTT ${MQTT_HOST:-nao}" s || erro "cancelado"
  fi
  local ARQ; ARQ=$(dpkg --print-architecture)
  . /etc/os-release

  msg "Pacotes do sistema"
  export DEBIAN_FRONTEND=noninteractive
  if [ "$ID" = debian ]; then  # driver de video da Intel com codificacao fica no non-free
    for f in /etc/apt/sources.list.d/*.sources; do
      [ -e "$f" ] && sed -i -E '/^Components:/ { / non-free( |$)/! s/$/ non-free/; /non-free-firmware/! s/$/ non-free-firmware/; / contrib( |$)/! s/^Components: main/Components: main contrib/ }' "$f"
    done
    [ -f /etc/apt/sources.list ] && sed -i -E '/^deb .* main/ { /non-free/! s/ main/ main contrib non-free non-free-firmware/ }' /etc/apt/sources.list
  fi
  apt-get update -qq || erro "apt-get update falhou"
  local P="sway swaybg xwayland python3-gi gir1.2-gtk-4.0 gir1.2-gtk4layershell-1.0 libgtk4-layer-shell0 gir1.2-adw-1
    python3-dbus python3-paho-mqtt fonts-material-design-icons-iconfont fonts-noto-core fonts-noto-color-emoji
    adwaita-icon-theme grim libglib2.0-bin dbus-user-session pipewire pipewire-pulse pipewire-alsa wireplumber
    libspa-0.2-bluetooth pulseaudio-utils bluez rtkit gnome-keyring xdg-desktop-portal-wlr xdg-desktop-portal-gtk
    curl ca-certificates vainfo mesa-va-drivers whiptail avahi-daemon"
  grep -qx 0x8086 /sys/class/drm/renderD*/device/vendor 2>/dev/null && P="$P intel-media-va-driver-non-free"
  [ "$RADIO" = 1 ] && P="$P shortwave gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav gstreamer1.0-pulseaudio"
  [ "$MPD" = 1 ] && P="$P mpd mpc"
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $P >> $LOG 2>&1 || {
    aviso "algum pacote falhou, tentando sem o driver non-free da Intel"
    apt-get install -y -q --no-install-recommends ${P/intel-media-va-driver-non-free/intel-media-va-driver} >> $LOG 2>&1 || erro "apt-get install falhou (veja $LOG)"; }
  ok

  msg "Programas da caixinha ($REPO@$REF)"
  baixa_fontes $DEST; ok "$DEST"

  msg "Usuario 'som' (roda o som, a tela e o Sunshine)"
  if ! id som >/dev/null 2>&1; then
    if getent passwd 2000 >/dev/null; then useradd -m -s /usr/sbin/nologin som
    else useradd -m -u 2000 -s /usr/sbin/nologin som; fi
  fi
  for g in audio video render input; do getent group $g >/dev/null || groupadd -r $g; usermod -aG $g som; done
  ok "uid $(id -u som)"

  msg "Sunshine (servidor do Moonlight)"
  if ! command -v sunshine >/dev/null; then
    local SUF; case "$ID-$VERSION_ID" in debian-13) SUF="debiantrixie_$ARQ";; ubuntu-*) SUF="ubuntu${VERSION_ID}_$ARQ";; *) SUF="debiantrixie_$ARQ";; esac
    local URL; URL=$(curl -fsSL https://api.github.com/repos/LizardByte/Sunshine/releases/latest | grep -o "https://[^\"]*${SUF}\.deb" | sed -n 1p)
    [ -n "$URL" ] || erro "nao achei o .deb do Sunshine para $SUF"
    curl -fsSL -o /tmp/sunshine.deb "$URL" && apt-get install -y -q /tmp/sunshine.deb >> $LOG 2>&1 || erro "instalacao do Sunshine falhou"
    rm -f /tmp/sunshine.deb
  fi; ok "$(dpkg-query -W -f='${Version}' sunshine)"

  if [ "$YTM" = 1 ] && ! command -v youtube-music >/dev/null; then
    msg "YT Music"
    local U; U=$(curl -fsSL https://api.github.com/repos/pear-devs/pear-desktop/releases/latest | grep -o "https://[^\"]*_${ARQ}\.deb" | sed -n 1p)
    if [ -n "$U" ] && curl -fsSL -o /tmp/ytm.deb "$U" && apt-get install -y -q /tmp/ytm.deb >> $LOG 2>&1; then ok
    else aviso "YT Music nao instalado (sem pacote para $ARQ?)"; fi
    rm -f /tmp/ytm.deb
  fi

  configura
  [ -r "$RESP" ] && rm -f "$RESP"  # respostas (com senhas) ja aplicadas
  if [ "${MODO:-}" = local ]; then final "$(hostname -I | awk '{print $1}')" "esta maquina"; fi
  return 0  # dentro do CT o host e quem mostra o final; sem isso a funcao saia com 1 e o host acusava falha
}

# ------------------------------------------------------------------ configuracao (instalar e atualizar)
configura() {
  local S=$DEST/sistema H=/home/som
  msg "Configurando"
  mkdir -p $CONF /var/lib/caixinha /var/log/caixinha
  chown som:som /var/lib/caixinha /var/log/caixinha

  # ambiente dos servicos
  local DBUS="unix:path=/run/dbus/system_bus_socket"
  [ "${EM_LXC:-0}" = 1 ] && [ "$BT" = 1 ] && DBUS="unix:path=/mnt/dbus-host/system_bus_socket"
  printf 'XDG_RUNTIME_DIR=/run/som\nDBUS_SYSTEM_BUS_ADDRESS=%s\nPULSE_SERVER=unix:/run/som/pulse/native\n' "$DBUS" > $CONF/ambiente

  # geral + MQTT (sem MQTT_HOST: a caixinha funciona so pela tela)
  [ -e $CONF/caixinha.env ] || printf 'NOME=%s\nNODE=\nSAIDA_LOCAL=%s\n' "$NOME" "$([ -n "${SOM_HW:-}" ] && echo caixinha_local)" > $CONF/caixinha.env
  if [ -n "${MQTT_HOST+x}" ] || [ ! -e $CONF/mqtt.env ]; then
    ( umask 027; printf '# Broker MQTT do Home Assistant (vazio = sem HA)\nMQTT_HOST=%s\nMQTT_PORT=%s\nMQTT_USER=%s\nMQTT_PASS=%s\n' \
      "${MQTT_HOST:-}" "${MQTT_PORT:-1883}" "${MQTT_USER:-}" "${MQTT_PASS:-}" > $CONF/mqtt.env )
    chown root:som $CONF/mqtt.env; chmod 640 $CONF/mqtt.env
  fi

  # GPU / codificador de video
  local RENDER="" ENC="encoder = software" SESS=""
  for r in /dev/dri/renderD*; do [ -e "$r" ] && { RENDER=$r; break; }; done
  if [ -n "$RENDER" ] && vainfo --display drm --device "$RENDER" 2>/dev/null | grep -q 'VAProfileH264.*EncSlice'; then
    ENC="encoder = vaapi
adapter_name = $RENDER"
    SESS="WLR_RENDER_DRM_DEVICE=$RENDER"
  elif command -v nvidia-smi >/dev/null && [ -e /dev/nvidia0 ]; then
    ENC="encoder = nvenc"
  fi
  [ -z "$SESS" ] && SESS="WLR_RENDERER=pixman"
  echo "$SESS" > $CONF/sessao.env
  # H.264 apenas: HEVC/AV1 por VAAPI falham em varias GPUs (ex.: Intel UHD 630)
  local MOD; MOD=$(<$S/sunshine.conf.modelo); MOD=${MOD//@NOME@/$NOME}
  printf '%s\n' "${MOD//@ENCODER@/$ENC}" > $CONF/sunshine.conf
  printf 'hevc_mode = 1\nav1_mode = 1\n' >> $CONF/sunshine.conf
  [ "$(echo "$ENC" | head -1)" = "encoder = software" ] && echo "sw_preset = superfast" >> $CONF/sunshine.conf
  cp $S/apps.json $CONF/apps.json
  local LS_LIB; LS_LIB=$(ls /usr/lib/*/libgtk4-layer-shell.so.0 2>/dev/null | head -1)
  sed -e "s/@RES@/${RES:-1280x720}/g" -e "s#@LAYERSHELL@#$LS_LIB#" $S/sway.conf.modelo > $CONF/sway.conf

  # credenciais do Sunshine (o servico le para aceitar o PIN do Moonlight pelo HA)
  if [ -n "${SUN_PASS:-}" ]; then
    ( umask 027; printf 'SUNSHINE_USUARIO=%s\nSUNSHINE_SENHA=%s\n' "$SUN_USER" "$SUN_PASS" > $CONF/sunshine.creds )
    chown root:som $CONF/sunshine.creds; chmod 640 $CONF/sunshine.creds
    runuser -u som -- sunshine $CONF/sunshine.conf --creds "$SUN_USER" "$SUN_PASS" >> $LOG 2>&1
  fi

  # som: PipeWire do usuario som como servico de sistema
  mkdir -p $H/.config/wireplumber/wireplumber.conf.d $H/.config/pipewire/pipewire.conf.d $H/.config/pipewire/pipewire-pulse.conf.d
  cp $S/wireplumber/*.conf $H/.config/wireplumber/wireplumber.conf.d/
  cp $S/pipewire/10-latencia.conf $H/.config/pipewire/pipewire.conf.d/
  cp $S/pipewire-pulse/10-latencia.conf $H/.config/pipewire/pipewire-pulse.conf.d/
  if [ -n "${SOM_HW:-}" ]; then
    sed -e "s/@HW@/$SOM_HW/" -e "s/@DESC@/${SOM_DESC:-Alto-falante local}/" $S/pipewire/50-saida-fixa.conf.modelo \
      > $H/.config/pipewire/pipewire.conf.d/50-saida-fixa.conf
  fi
  chown -R som:som $H/.config

  # servicos e utilitarios
  for u in caixinha-pipewire caixinha-wireplumber caixinha-pipewire-pulse caixinha-som-vigia caixinha-bt multimidia-sessao; do
    install -m 644 $S/$u.service /etc/systemd/system/$u.service
  done
  for b in caixinha-som-vigia multimidia-sunshine abrir-pagina tela-foto swaymsg-caixinha; do install -m 755 $S/$b /usr/local/bin/$b; done
  # o PipeWire do sistema (sessao de usuario) nao pode brigar com o da caixinha
  systemctl --global disable pipewire.socket pipewire-pulse.socket wireplumber.service >/dev/null 2>&1

  if [ "$MPD" = 1 ] && command -v mpd >/dev/null; then
    sed "s/@NOME@/$NOME/g" $S/mpd.conf.modelo > /etc/mpd.conf
    mkdir -p /etc/systemd/system/mpd.service.d
    printf '[Unit]\nAfter=caixinha-pipewire-pulse.service\nWants=caixinha-pipewire-pulse.service\n' > /etc/systemd/system/mpd.service.d/caixinha.conf
    mkdir -p /var/lib/mpd/music /var/lib/mpd/playlists; chown -R som /var/lib/mpd
  fi

  if [ "${EM_LXC:-0}" = 1 ]; then
    # sem udevd no LXC: banco do udev do host + avisos de hotplug (udev-inject) chegam de fora
    echo "L+ /run/udev - - - - /mnt/hostudev" > /etc/tmpfiles.d/udev-host.conf
    systemd-tmpfiles --create /etc/tmpfiles.d/udev-host.conf 2>/dev/null
    install -m 755 $DEST/host/udev-inject /usr/local/bin/udev-inject
    systemctl mask bluetooth.service >/dev/null 2>&1   # o bluez roda no host
    # rtkit no LXC morre ao trocar de usuario ("File size limit exceeded")
    mkdir -p /etc/systemd/system/rtkit-daemon.service.d
    printf '[Service]\nExecStart=\nExecStart=/usr/libexec/rtkit-daemon --no-drop-privileges\n' > /etc/systemd/system/rtkit-daemon.service.d/lxc.conf
  elif [ "$BT" = 1 ]; then
    systemctl enable --now bluetooth >/dev/null 2>&1
  fi
  ln -sf /usr/share/zoneinfo/"$(cat /etc/timezone 2>/dev/null || echo America/Sao_Paulo)" /etc/localtime 2>/dev/null

  systemctl daemon-reload
  local SV="caixinha-pipewire caixinha-wireplumber caixinha-pipewire-pulse caixinha-som-vigia caixinha-bt multimidia-sessao avahi-daemon"
  [ "$MPD" = 1 ] && command -v mpd >/dev/null && SV="$SV mpd"
  # shellcheck disable=SC2086
  systemctl enable $SV >/dev/null 2>&1
  systemctl restart rtkit-daemon >/dev/null 2>&1
  # shellcheck disable=SC2086
  systemctl restart $SV >> $LOG 2>&1 || aviso "algum servico nao subiu (systemctl status multimidia-sessao)"
  ok
}

final() { # final <ip> <onde>
  local IP=$1
  sleep 3
  echo
  echo "${C_OK}Pronto!${C_0} A caixinha esta rodando em $2."
  echo
  echo "  Moonlight:  adicione o host ${C_B}$IP${C_0} (aparece sozinho na rede como '$NOME')."
  echo "              Ao parear, o Moonlight mostra um PIN de 4 numeros: digite em"
  echo "              https://$IP:47990  (usuario: $SUN_USER  senha: $SUN_PASS)"
  [ -n "${MQTT_HOST:-}" ] && echo "              ou no Home Assistant, no campo 'PIN do Moonlight' do dispositivo '$NOME'."
  [ "${BT:-0}" = 1 ] && echo "  Bluetooth:  na tela, botao Bluetooth da barra -> Visivel -> parear o celular (codigo aparece na tela)."
  [ -z "${SOM_HW:-}" ] && echo "  Som:        sem placa local - o audio sai pelo proprio Moonlight$([ "${BT:-0}" = 1 ] && echo " ou por uma caixa Bluetooth")."
  [ "${MPD:-0}" = 1 ] && echo "  MPD:        no HA, Adicionar integracao -> Music Player Daemon (MPD), host $IP porta 6600 (sem senha)."
  echo "  Configs:    $CONF/  (MQTT: mqtt.env)    Atualizar: bash $DEST/install.sh --atualizar"
  echo
}

atualizar() {
  [ -d $DEST ] || erro "a caixinha nao esta instalada aqui"
  msg "Atualizando os programas ($REPO@$REF)"
  baixa_fontes $DEST; ok
  NOME=$(sed -n 's/^NOME=//p' $CONF/caixinha.env | head -n1 | tr -d "\"'"); NOME=${NOME:-Caixinha}  # sem 'source': o nome pode ter espaco
  RES=$(sed -n 's/^output HEADLESS-1 mode \([0-9x]*\)@.*/\1/p' $CONF/sway.conf); RES=${RES:-1280x720}
  EM_LXC=0; [ -e /etc/tmpfiles.d/udev-host.conf ] && EM_LXC=1
  BT=0; grep -q mnt/dbus-host $CONF/ambiente 2>/dev/null && BT=1; systemctl is-enabled bluetooth >/dev/null 2>&1 && BT=1
  MPD=0; command -v mpd >/dev/null && MPD=1
  SOM_HW=""; unset MQTT_HOST SUN_PASS
  [ -e /home/som/.config/pipewire/pipewire.conf.d/50-saida-fixa.conf ] && cp /home/som/.config/pipewire/pipewire.conf.d/50-saida-fixa.conf /tmp/saida.conf
  configura
  [ -e /tmp/saida.conf ] && mv /tmp/saida.conf /home/som/.config/pipewire/pipewire.conf.d/50-saida-fixa.conf && chown som:som /home/som/.config/pipewire/pipewire.conf.d/50-saida-fixa.conf
  ok "atualizado"
}

# ------------------------------------------------------------------ entrada
case "${1:-}" in
  --alvo)      instala_alvo ;;
  --atualizar) atualizar ;;
  *)
    if command -v pveversion >/dev/null 2>&1; then instala_proxmox
    else instala_alvo; fi ;;
esac
