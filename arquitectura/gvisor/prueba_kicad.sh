#!/bin/sh
# prueba_kicad.sh — verifica kicad-cli exporta esquemático a PDF dentro del sandbox.

echo "=== kicad-cli en sandbox ==="
kicad-cli version 2>&1 | head -2

# workdir en /tmp (tmpfs escribible)
WORK=/tmp/kicad_test
mkdir -p "$WORK"
cd "$WORK"

# esquemático .kicad_sch mínimo (un resistor genérico, autocontenido)
cat > esq.kicad_sch <<'EOF'
(kicad_sch (version 20240108) (generator eeschema)
  (paper "A4")
  (lib_symbols
    (symbol "R1" (pin_numbers hide) (pin_names (offset 0)) (exclude_from_sim no)
      (in_bom yes) (on_board yes)
      (property "Reference" "R1" (at 0 -7.62 0) (effects (font (size 1.27 1.27))))
      (property "Value" "1k" (at 0 7.62 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (symbol "R1_0_1" (rectangle (start -2.54 -1.27) (end 2.54 1.27) (stroke (width 0.254)))
        (pin passive line (at -5.08 0 0) (length 2.54) (name "1" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 5.08 0 180) (length 2.54) (name "2" (effects (font (size 1.27 1.27))))
          (number "2" (effects (font (size 1.27 1.27))))))))
  (symbol (lib_id "R1") (at 0 0 0) (unit 1) (in_bom yes) (on_board yes)
    (uuid 00000000-0000-0000-0000-000000000001)
    (property "Reference" "R1" (at 0 -7.62 0) (effects (font (size 1.27 1.27))))
    (property "Value" "1k" (at 0 7.62 0) (effects (font (size 1.27 1.27))))))
EOF

echo "=== exportar a PDF ==="
kicad-cli sch export pdf -o esq.pdf esq.kicad_sch 2>&1 | head -10
RC=$?
if [ -f esq.pdf ]; then
  echo "CHECK_POS_kicad_ok (PDF generado: $(ls -la esq.pdf | awk '{print $5}') bytes)"
else
  echo "CHECK_POS_kicad_fail (no se generó PDF, rc=$RC)"
fi

echo "KICAD_DONE"
