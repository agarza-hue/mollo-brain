# Principal TELOS — Adolfo

> Regenerado: 2026-05-11 20:42 UTC. Auto-generado por `telos_service.regenerate_summary()`. NO editar a mano.

## Misiones

- **M0:** Construir personal AI infra que dé leverage real en mi vida y trabajo, sin depender de SaaS opacas que se rentan y desaparecen.
- **M1:** Owner-operator stack — construyo lo que uso, valido en mi propia vida primero, y solo después escalo a otros (Sinergy, clientes).
- **M2:** Reemplazar herramientas de $30/mes (subscriptions, dashboards, coaching) con código propio que entiende mi contexto MX/Spanish y mis goals reales.

## Goals activos

- **G0:** MolloTV en FireTV/AndroidTV reemplazando 100% el uso de Nice IPTV — criterio: APK instalado, canales live + VOD reproduciendo, EPG funcional, familia usándolo sin queja por 7 días seguidos. Deadline duro: **2026-05-30** (license Nice expira).
- **G1:** Mollo OS a producción usable diariamente — criterio: dashboard en https://app.mollo-ai.com/os muestra TELOS + Mollo chat + Vanta + Juntas en un solo sitio, login funcional, abierto al menos 1x/día.
- **G2:** Vantamedia Plataforma Financiera v4 cierre de P2 pendientes — criterio: lista de P2 de la memoria `project_vanta_plataforma.md` resuelta y deployed en nginx:8090.
- **G3:** Juntas & Pendientes operando para mí y al menos 1 cliente externo — criterio: app en producción, juntas creadas vía API, accionables exportables.
- **G4:** Sinergy/Mollo brain como SaaS multi-tenant con 1 cliente pagando — criterio: Stripe live activo, tenant aislado, billing automático.
- **G5:** Pipeline de software a medida — cerrar 2 clientes más para totalizar 5 antes de 2026-12-31. Criterio: contrato firmado + primer pago. Ticket promedio target: $25,000 MXN/proyecto. Base actual: 3 clientes activos.

## Problemas que resuelvo

- **P0:** Las apps de productividad AI cobran $30/mes por features que toman 4 hrs de setup propio. Compounding mensual contra alguien que sabe instalar un repo.
- **P1:** Mi sub IPTV upstream (Nice) tiene horizonte de licencia (expira 2026-05-30) y precio creciente cada renovación. Reemplazar con IPTV-Manager + MolloTV.
- **P2:** Fragmentación: Mollo chat vive en /mollo, MolloTV en /app/, Vanta en :8090, Juntas en :80. Cada app es silo; no hay un único lugar donde ver "mi estado".
- **P3:** Los SaaS multi-tenant cobran caro pero los clientes no pueden customizar el modelo o el contexto. Mollo brain ofrece eso pero falta auth/billing/onboarding pulido.
- **P4:** No hay visibilidad de mis goals/decisions a lo largo del tiempo — la memoria de Claude Code se borra, las decisiones de Mollo no se loggean centralizadas, Vanta no muestra histórico financiero unificado.

## Estrategias

- **S0:** Self-host > SaaS cuando hay un FOSS activo con licencia compatible (MIT/Apache). Hecho con IPTV-Manager reemplazando Nice; aplicable también a Stremio, Restreamer.
- **S1:** Fork FOSS antes de construir desde cero. StreamVault → MolloTV.apk. PAI → robar 3 ideas (Telos/ISA/Guard) en Mollo. Reusa, no reinventes.
- **S2:** Ship cruda v1, iterar en público y validar antes de polish. Validate antes de continuar: tras cada cambio UI sustantivo, frenar para verificar en browser.
- **S3:** Spanish-first MX context en todo lo user-facing. Labels, naming, copy. Mollo y MolloTV nunca son "Search/Home/Settings".
- **S4:** Routing de modelos por costo: GPT-4o-mini para tareas auxiliares (test prompts, etiquetar, transformar texto), GPT-4o para medio, Sonnet solo cuando complejo lo justifica. Nunca quemar Sonnet en código boilerplate.
- **S5:** Scope antes de barrer. Para diagnósticos amplios usar AskUserQuestion por dimensión, no multi-bash sweep. Para acciones destructivas, confirmar antes.
- **S6:** Memoria persistente en markdown plano (auto-memory + TELOS). Lees con `cat`, buscas con `rg`, versionas con `git`. Cero vector DBs para identity/goals — solo para semantic search de docs.
- **S7:** ROI counterfactual explícito: para reportar valor de un sistema, separar (a) lo que cambió, (b) qué hubiera pasado sin él, (c) cuál es el delta verificable.

## Narrativas activas

- **N0:** Owner-operator stack: construyo lo que uso. Si Mollo o MolloTV no resuelven mi propio problema primero, no merecen existir. Eat my own dogfood.
- **N1:** Mi cerebro corre en mi VPS, no en la nube de alguien más. Los datos, las decisiones, la memoria — todo bajo mi control físico. Sin "cuenta" que pueden cerrar.
- **N2:** El futuro del software personal es 1 Digital Assistant por persona (TRIOT — Miessler 2016). Mollo es mi DA. Mollo OS es la interfaz. MolloTV es un app de ese ecosistema.
- **N3:** Cada repo que público o monetizo es prueba de concepto de la narrativa. Sinergy = "Mollo para empresas". Juntas = "productividad sin SaaS". Vanta = "finanzas sin Excel cloud".

## Challenges personales

- **C0:** Tendencia a abrir proyectos en paralelo antes de cerrar el anterior. Inventario actual: Mollo + Mollo OS + mollo-web + MolloTV web + MolloTV apk + Vanta + Juntas + Sinergy + Excel platform + Strategy OS. Riesgo: ninguno llega a v1 acabada.
- **C1:** Bias a "ship cruda v1" puede degradar en deuda técnica si no se cierra el loop con el polish necesario. Aplicar la estrategia "ship cruda" requiere disciplina para volver y consolidar antes del próximo proyecto.

---
*Este archivo es la vista comprimida del usuario. Mollo lo usa para priorizar, sugerir, y mantener alineadas las respuestas con los goals reales.*
