#!/bin/sh
# firewall_hcma_proxy.sh — firewall nftables de egreso POR UID del proxy (hcma-proxy, UID 997).
#
# Dictamen Fable (fable_proxy_red_fable.md §2): "el default-deny real va en kernel: nftables por
# UID del proxy, solo tcp/443 a rangos globales y DNS solo al resolver designado."
#
# El firewall en CÓDIGO (hcma_proxy._ip_permitida) es defensa en profundidad; ESTA es la capa de
# enforcement en kernel independiente del proceso.
#
# ⚠️ LECCIÓN (incidente 2026-11-15): la cadena output NO debe usar 'policy drop', porque el hook
# output aplica a TODO el egreso del host, no solo al UID del proxy. 'policy drop' cortó internet
# a toda la máquina. El diseño correcto es policy ACCEPT + una regla final 'skuid <proxy> drop'
# que restringe SOLO al proxy. No reintroducir 'policy drop' en output.
#
# Política (v1, IPv4-only):
#   - host normal: sin cambios (policy accept).
#   - UID 997 (hcma-proxy):
#       * udp/53 y tcp/53 SOLO al resolver designado (DNS).
#       * tcp/443 SOLO a unicast global IPv4 público.
#       * el resto se dropea (default-deny SOLO para el proxy).
#
# Uso:  sudo sh firewall_hcma_proxy.sh apply|show|flush
set -eu

NFT="/usr/sbin/nft"
PROXY_UID="997"
TABLA="hcma_egreso"
DNS_RESOLVER="192.168.1.1"

# Rangos IPv4 de unicast global PÚBLICO (mismo criterio que hcma_proxy._ip_permitida:
# is_global del módulo ipaddress, restando reservados). Generado por colapso de /8 no solapados.
GLOBAL_V4="1.0.0.0/8 101.0.0.0/8 102.0.0.0/7 104.0.0.0/5 11.0.0.0/8 112.0.0.0/5 12.0.0.0/6 120.0.0.0/6 124.0.0.0/7 126.0.0.0/8 128.0.0.0/2 16.0.0.0/4 193.0.0.0/8 194.0.0.0/7 196.0.0.0/7 199.0.0.0/8 2.0.0.0/7 200.0.0.0/5 208.0.0.0/4 32.0.0.0/3 4.0.0.0/6 64.0.0.0/3 8.0.0.0/7 96.0.0.0/6"

apply() {
  $NFT flush ruleset
  $NFT add table inet "$TABLA"
  # set de unicast global público (interval)
  $NFT add set inet "$TABLA" global_v4 '{ type ipv4_addr; flags interval; }'
  for r in $GLOBAL_V4; do
    $NFT add element inet "$TABLA" global_v4 "{ $r }"
  done

  # Cadena de egreso. IMPORTANTE (bug corregido): policy debe ser ACCEPT para el host normal.
  # El default-deny se aplica SOLO al UID del proxy con una regla final 'skuid 997 drop'.
  # NUNCA usar 'policy drop' en output hook: bloquearía TODO el egreso del host, no solo el proxy.
  $NFT add chain inet "$TABLA" egreso_proxy '{ type filter hook output priority 0; policy accept; }'

  # DNS: solo al resolver designado (solo el proxy)
  $NFT add rule inet "$TABLA" egreso_proxy meta skuid "$PROXY_UID" udp dport 53 ip daddr "$DNS_RESOLVER" accept
  $NFT add rule inet "$TABLA" egreso_proxy meta skuid "$PROXY_UID" tcp dport 53 ip daddr "$DNS_RESOLVER" accept

  # HTTPS: tcp/443 solo a unicast global público (solo el proxy)
  $NFT add rule inet "$TABLA" egreso_proxy meta skuid "$PROXY_UID" tcp dport 443 ip daddr @global_v4 accept

  # Default-deny SOLO para el proxy: todo lo demás del UID 997 se dropea.
  # El resto del host (otros UIDs) NO es tocado (policy accept).
  $NFT add rule inet "$TABLA" egreso_proxy meta skuid "$PROXY_UID" drop

  echo "OK: firewall nftables aplicado (default-deny SOLO UID $PROXY_UID; host intacto)"
}

show() {
  $NFT list ruleset
}

flush_() {
  $NFT flush ruleset
  echo "OK: ruleset nftables vaciado"
}

case "${1:-apply}" in
  apply) apply ;;
  show) show ;;
  flush) flush_ ;;
  *) echo "uso: $0 apply|show|flush"; exit 2 ;;
esac
