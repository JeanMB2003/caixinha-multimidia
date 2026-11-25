# caixinha-multimidia

Central de som no estilo **multimídia de carro**, sem monitor: uma tela virtual (relógio grande, atalhos
redondos, barra com HOME / volume / faixa) que você usa pelo **Moonlight** no PC, celular ou tablet.

- **Bluetooth**: o celular toca na caixinha (título/artista e controles aparecem na central; o volume do
  celular e o da caixinha andam juntos) ou a caixinha toca numa caixa Bluetooth. PIN de pareamento na tela.
- **Reprodutores**: Rádio (Shortwave), YT Music, MPD — um tocando por vez.
- **Painel flutuante** (botão Bluetooth da barra) com reprodutor, volume, saída e aparelhos, e opção de
  expandir para a tela inteira.
- **Home Assistant** (opcional, MQTT Discovery): saída de áudio, volume, Bluetooth, saída do MPD e o campo
  **PIN do Moonlight** — para parear um Moonlight novo direto pelo HA.
- Vídeo codificado pela GPU (Intel/AMD via VAAPI) ou pela CPU quando não há GPU.

## Instalar

No **Proxmox VE** (cria um container LXC) ou num **Debian 13 / Ubuntu 24.04** (instala na própria máquina),
como root:

```sh
bash -c "$(curl -fsSL https://raw.githubusercontent.com/JeanMB2003/caixinha-multimidia/main/install.sh)"
```

- **Padrão (recomendado)**: pergunta só se você quer ligar ao Home Assistant. Container com 2 GB de RAM,
  4 núcleos, 16 GB, DHCP; a GPU, a placa de som USB e o Bluetooth do host são detectados sozinhos.
- **Avançado**: escolhe ID, nome, RAM, CPU, disco, armazenamento, rede/IP fixo, GPU, placa de som,
  Bluetooth, resolução (720p/1080p/1024×600), apps, MPD, usuário/senha do Sunshine e MQTT.
- Sem perguntas (tudo no padrão, sem MQTT): `CAIXINHA_AUTO=1 bash -c "$(curl ...)"`

Ao final ele mostra o IP e a senha do painel do Sunshine. No Moonlight, adicione o host (ele também aparece
sozinho na rede); o PIN que o Moonlight mostrar vai em `https://IP:47990` ou no campo **PIN do Moonlight** do HA.

## Atualizar

Dentro da caixinha: `bash /opt/caixinha-multimidia/install.sh --atualizar` (mantém as configurações).

## Onde fica cada coisa

| Caminho | O quê |
|---|---|
| `/etc/caixinha/mqtt.env` | broker do Home Assistant (vazio = sem HA) |
| `/etc/caixinha/caixinha.env` | nome, ID no MQTT, saída local |
| `/etc/caixinha/sunshine.conf`, `sway.conf` | codificador, resolução |
| `/opt/caixinha-multimidia/app/` | a interface (`multimidia.py`) e o serviço (`caixinha_bt.py`, API local `127.0.0.1:8095`) |
| `/var/log/caixinha/` | logs da tela, do Sunshine e dos apps |
| `abrir-pagina`, `tela-foto` | trocar de tela / tirar foto da tela virtual pelo terminal |

No Proxmox o host recebe também `multimidia-input` (+ regra do udev): leva o teclado/mouse/toque virtuais do
Moonlight para dentro do container sem privilégios.

## Observações

- Spotify: clientes livres (librespot) não tocam mais com contas recentes — use o celular por Bluetooth.
- NVIDIA dentro do LXC não é configurada pelo instalador (o vídeo fica na CPU); instalação direta numa máquina
  com o driver NVIDIA usa NVENC.
- Numa máquina com desktop, o instalador desliga o PipeWire da sessão do usuário (a caixinha usa o seu próprio).
- Sem placa de som (nem Bluetooth) a caixinha funciona igual: o áudio sai pelo próprio Moonlight.
- MPD no Home Assistant: *Adicionar integração → Music Player Daemon (MPD)*, host = IP da caixinha, porta 6600.
  Para tocar pelo Music Assistant, ative o provedor *Home Assistant MediaPlayers* e marque esse player.
- Várias caixinhas (ou outro Sunshine) no mesmo Proxmox: o teclado/mouse/toque virtuais do Moonlight vão
  para todas as caixinhas do host — o kernel não diz qual Sunshine criou cada dispositivo.
- Moonlight com erro 409 ou que não conecta depois de parear: exclua o PC no Moonlight, adicione o IP de novo
  e pareie (no Sunshine, *Clientes → Desparear todos* limpa os pareamentos antigos).
