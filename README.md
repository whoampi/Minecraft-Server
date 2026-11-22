# TEL342 — Servidor Minecraft seguro tras firewall (IaC)

Proyecto de **Administración de Redes de Computadores** (UTFSM). Un servicio de
Minecraft expuesto de forma controlada tras un **gateway Debian con firewall
nftables en dos fases**, **automatizado con Ansible**, **observado con
Prometheus + Grafana**, que demuestra **Log4Shell (CVE-2021-44228)** mediante el
*callback* del exploit, y su mitigación.

Este repositorio es **infraestructura como código**: se clona y un solo
`ansible-playbook` construye todo desde cero.

---

## Arquitectura (resumen)

```
Internet ──(túnel playit.gg)──► blackwall (host/atacante, 192.168.56.1)
                                     │ Host-Only vboxnet0 192.168.56.0/24
                                     ▼
                         gw (gateway/firewall)  enp0s3 192.168.56.10
                                                enp0s8 10.10.10.1
                                     │ red interna svmc-priv 10.10.10.0/24
                                     ▼
                         mc (servidor)          10.10.10.10
                           ├─ Minecraft 26.3  (25565, RCON 25575)  JVM 25
                           └─ Minecraft 1.18  (25566, RCON 25576)  JVM 17 (vulnerable)
```

Detalle exhaustivo (reglas de firewall, Log4Shell, monitoreo, IPs y puertos):
**`docs/INFORME-TECNICO-COMPLETO.md`**.

---

## Requisitos previos

1. **Dos VMs Debian 13 (trixie)**, con la red de VirtualBox ya puesta:
   - `gw`: adaptador 1 = Host-Only (`vboxnet0`, `192.168.56.0/24`), adaptador 2 = Red interna `svmc-priv`.
   - `mc`: adaptador 1 = Red interna `svmc-priv`.
   - En el host: `VBoxManage hostonlyif create` + `VBoxManage hostonlyif ipconfig vboxnet0 --ip 192.168.56.1 --netmask 255.255.255.0`.
2. **SSH habilitado** en ambas; usuarios `jbjp_gw` / `jbjp_mc` con sudo.
3. **Internet en ambas VMs durante la provisión** (p. ej. un adaptador NAT
   temporal): se instalan paquetes y se descargan los `server.jar` de Mojang.
   Terminada la provisión, el adaptador NAT puede quitarse (la demo funciona
   solo con Host-Only + red interna).
4. En el nodo de control (tu host): `ansible` instalado.

---

## Construir todo desde cero (3 comandos)

```bash
git clone <URL-del-repo> tel342-minecraft-firewall
cd tel342-minecraft-firewall
ansible-playbook provision.yml -K        # -K pide la password de sudo
```

(`ansible.cfg` ya apunta a `inventory.ini`, así que no hace falta `-i`.)

Al terminar:
- Gateway con firewall en **fase1**, `nft-metrics`, Prometheus y Grafana.
- **Grafana:** `http://192.168.56.10:3000` (admin / `1234`), dashboard ya cargado.
- Servidor con las **dos instancias** de Minecraft corriendo y el exportador de
  jugadores por RCON.

Los `server.jar` **no están en el repo**: el playbook los descarga del
**manifiesto oficial de Mojang** (buscando los id `26.3` y `1.18`) verificando
su `sha1`.

---

## Correr la demo

```bash
ansible-playbook demo.yml --tags prep       -K   # levanta servidores, rutas, RCON
ansible-playbook demo.yml --tags fase1      -K   # firewall permisivo (vulnerable)
ansible-playbook demo.yml --tags scan            # nmap: 25565-25580 abiertos
ansible-playbook demo.yml --tags log4shell  -K   # callback SALE (Grafana: c_callback sube)
ansible-playbook demo.yml --tags fase2      -K   # firewall endurecido (seguro)
ansible-playbook demo.yml --tags scan            # nmap: solo 25565
ansible-playbook demo.yml --tags log4shell  -K   # callback BLOQUEADO (c_fwd_drop sube)
ansible-playbook demo.yml --tags flood      -K   # sobrecarga a puertos bloqueados
ansible-playbook demo.yml --tags reset      -K   # volver a baseline
```

El jugador externo entra por el túnel **playit.gg** (sistema aparte en el host).
Para ver el callback en vivo en el gateway: `sudo tcpdump -ni enp0s8 tcp port 1389`.

---

## Estructura

```
.
├── provision.yml      # construye TODO desde cero (gateway + servidor)
├── demo.yml           # orquesta la demo (actos por --tags)
├── inventory.ini      # gw (directo), mc (ProxyJump por gw), blackwall (local)
├── ansible.cfg
├── tasks/
│   └── descargar-jar.yml   # resuelve y baja cada server.jar desde Mojang (sha1)
├── files/
│   ├── dashboard.json      # dashboard de Grafana (uid adksz7p)
│   └── rcon.py             # cliente RCON usado por la demo
└── docs/
    └── INFORME-TECNICO-COMPLETO.md   # referencia completa del proyecto
```

---

## Notas

- **`openjdk-25-jre-headless`**: si no está en el repo base de trixie, habilita backports:
  ```bash
  echo 'deb http://deb.debian.org/debian trixie-backports main' | sudo tee /etc/apt/sources.list.d/backports.list
  sudo apt update
  ```
- **Firewall persistente:** `provision.yml` instala `firewall-fase.service`, que
  re-aplica **fase1** en cada boot (arregla el *flush* de nftables). Para "seguro
  por defecto", cambia `fase1` → `fase2` en su `ExecStart`.
- **Mundos:** el primer arranque **genera mundos nuevos**; no se incluyen los
  `world/` originales (si los quieres exactos, cópialos aparte).
- **Secretos:** credenciales de laboratorio (`admin123`, Grafana `1234`). No usar
  tal cual en producción.
- **Idempotente:** `provision.yml` se puede re-correr; solo cambia lo necesario.
