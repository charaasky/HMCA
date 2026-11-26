# Arquitectura: Orquestador + HCMA + IAs especializadas

**Estado:** diseño inicial (pensándolo juntos)
**Fecha:** 2026-11-02
**Serie:** HCMA (dirección potencial para paper 6)

---

## 1. Diagrama

```mermaid
flowchart TD
    U[Usuario: ingeniero / programador] -->|1. tarea + criterios de aceptación| O[Orquestador IA]
    O -->|2. descompone en procedimientos| P[Plan versionado]
    O -->|3. deriva cada paso| S[IAs especializadas vía OpenRouter]
    S -->|4. resultado ESTRUCTURADO (JSON/esquema)| H[HCMA: memoria gobernada]
    H -->|5. estado consolidado vigente| O
    O -->|6. bucle hasta terminar| O
    O -->|7. documento final (memoria, planos, presupuesto)| U
    U -->|8. aprueba o corrige| O
    P -.->|el plan también es un hecho versionado| H
```

---

## 2. Componentes

| Componente | Rol | Notas |
|-----------|-----|-------|
| **Usuario** | Da la tarea y los criterios de aceptación; aprueba/corrige al final | ingeniero (puente) o programador (software) |
| **Orquestador IA** | Descompone la tarea en una lista de procedimientos, deriva cada paso a un especialista, controla el bucle, ensambla el documento final | el "sistema 2" deliberativo |
| **HCMA** | Memoria gobernada = single-source-of-truth **versionado** del proyecto | hechos, decisiones, cálculos, con autoridad + versión |
| **IAs especializadas** | Dominios: estructural, materiales, ambiental, normativa/legal, presupuesto | vía OpenRouter (pruebas) |

---

## 3. Rol de HCMA (lo que lo distingue de "shared context + RAG")

HCMA **no** es un bus de mensajes ni un chat compartido. Es el **estado del proyecto como
hechos versionados**:

- Cada resultado de un especialista se escribe como **hechos estructurados** con
  `fact_key` + `value` + `version` + `authority`.
- La **gobernanza determinista** (descartar `authority=False` + `max(version)`) garantiza que
  el orquestador solo vea la **última versión vigente** de cada dato.
- **Corrección del ingeniero = nueva versión que supersede** la anterior (o `mark_invalid` +
  rollback), no un borrado.
- **Auditabilidad**: tamper-evident log, DAG de correcciones, gobernable deletion (derecho al
  olvido si una norma lo exige).

Esto es lo que un RAG plano **no** da: no hay noción de "qué versión es la vigente", y si un
especialista corrige un dato, el RAG mezcla la versión vieja con la nueva.

---

## 4. Decisiones de diseño clave (grounded en los hallazgos de HCMA)

### 4.1 El write path acá es SEMI-ESTRUCTURADO (la gran diferencia con E9)

En E9 el cuello de botella era **extraer metadata desde texto conversacional** (57% vs 100%
oráculo). Acá la situación es distinta:

- A cada especialista se le exige devolver **JSON/esquema** (no prosa): `{"beam": "W36x135",
  "span_m": 24.0, "load_kN": 1230, ...}`.
- El write path es **explícito por diseño** (como Scion: el diff ya viene estructurado), no hay
  que "extraer" desde texto.
- → La gobernanza (oráculo) debería operar a ~100%, **sin** el cuello de botella de E9.

**Esto es la hipótesis central de la arquitectura:** *el problema de E9 (extracción) se
desvanece si forzamos un write path estructurado desde el contrato de salida de cada agente.*

### 4.2 El plan también es un hecho versionado

La lista de procedimientos del orquestador se guarda en HCMA como hechos (`procedimiento N`,
estado `pendiente/en_curso/hecho`, `dueño = especialista X`). Así el bucle tiene **estado
persistente y auditable**, no solo el contexto de la última conversación.

### 4.3 Aprobación = autoridad

Cuando el ingeniero aprueba un dato, se marca `authority=True` y se congela. Si corrige, se
crea una nueva versión con `authority=True` y la vieja pasa a `authority=False`. Esto es
exactamente el `version collapse` que ya mediste (oráculo 100%).

---

## 5. Riesgos (los que hay que testear)

1. **Propagación de errores**: una IA especializada alucina un valor → contamina el estado →
   las siguientes iteraciones lo toman como cierto. → mitigación: `authority` + verificación
   cruzada + abstención conformal (paper 4) sobre outputs de baja confianza.
2. **Calidad de la descomposición**: si el orquestador arma mal la lista de procedimientos, todo
   el bucle falla. → mitigación: el plan es revisable/corregible como hecho versionado.
3. **Consistencia a través del bucle**: N iteraciones → deriva de estado. → mitigación: version
   collapse + snapshot congelado por iteración.
4. **Costo/explosión de tokens**: N especialistas × M iteraciones. → mitigación: solo el estado
   consolidado (no todo el historial) va al contexto de cada agente.
5. **Deadlock del bucle**: el orquestador no termina. → mitigación: criterio de parada explícito
   (todos los procedimientos `hecho` + aprobación del usuario).

---

### 4.5 Procurement: qué comprar y dónde (Argentina) — drift temporal real

Una vez que el especialista de materiales devuelve la lista, aparece el paso de **compras**:

- Hay **muchas webs con precios**; el sistema debe decidir **qué comprar** (especificación
exacta: viga IPN 200 S235JR) y **dónde** (proveedor en Argentina: Acindar/Ternium/Siderar,
Sodimac/Easy, ferreterías mayoristas, etc.).
- **Los precios son un drift temporal real**: cambian con el tiempo y por proveedor. Esto es
exactamente el problema que HCMA resuelve: cada precio es un hecho versionado
(`precio_viga_ipn200` → `{proveedor, precio, fecha, authority}`), y el version collapse
garantiza que se use el **último precio vigente**, no uno viejo.
- El paso de compras es **localización-dependiente** (Argentina): el especialista de compras
debe saber qué proveedores existen acá y emitir una decisión `{qué, dónde, precio, fuente}`.
- La búsqueda de precios requiere una **API de búsqueda real** (Tavily/SerpAPI) o scraping de
sitios de proveedores; en el prototipo se simula, pero el contrato queda listo para pluguear la
API.


No arrancar con un puente completo. Probar primero una **tarea más chica pero real**, que tenga
los mismos ingredientes (descomposición + especialistas + corrección + versión):

**Candidato 1 — mini-proyecto estructural**: "diseñar una viga simplemente apoyada" →
- Especialista estructural (cálculo de momento/cortante/sección).
- Especialista materiales (acero vs hormigón, costos).
- Especialista normativo (código aplicable).
- El ingeniero corrige la sección → nueva versión → el orquestador re-ensambla.

**Candidato 2 — mini-programa**: "función que parsea X con tests" → especialistas: diseño,
implementación, tests, review.

**Candidato 3 — presupuesto de materiales** de una lista de ítems con precios que cambian
(versiones) y aprobación del ingeniero.

---

## 7. Experimento falsable (para demostrar que HCMA aporta, no marketing)

Comparar dos brazos sobre la MISMA tarea multi-paso con correcciones:

| Brazo | Memoria |
|-------|---------|
| `A_shared_context` | contexto plano compartido (lo que un agente con RAG/mensajes usaría) |
| `B_hcma` | memoria gobernada (version collapse + autoridad) |

**Métrica principal:** tasa de uso de valor obsoleto tras una corrección (el "stale-use" de la
serie, pero ahora en un flujo de trabajo multi-agente).

**Hipótesis falsable:** `B_hcma` tiene stale-use = 0% y el documento final usa la versión
aprobada; `A_shared_context` re-mezcla versiones viejas y produce el valor obsoleto.

Si `B_hcma` no gana, la arquitectura no aporta sobre shared-context, y eso también es un
resultado honesto.

---

## 8. Pendientes / decisiones

- [ ] Consultar a Opus (el script `consult_opus_arquitectura.py` está listo).
- [ ] Definir el contrato JSON exacto (schema) que cada especialista debe devolver.
- [ ] Elegir la tarea MVP (candidato 1, 2 o 3).
- [ ] Decidir si es paper 6 o un prototipo interno primero.
