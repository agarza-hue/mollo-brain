# Estrategias

> Cómo abordas los problemas. Heurísticas estables que aplicas independiente del proyecto.

- **S0:** Self-host > SaaS cuando hay un FOSS activo con licencia compatible (MIT/Apache). Hecho con IPTV-Manager reemplazando Nice; aplicable también a Stremio, Restreamer.
- **S1:** Fork FOSS antes de construir desde cero. StreamVault → MolloTV.apk. PAI → robar 3 ideas (Telos/ISA/Guard) en Mollo. Reusa, no reinventes.
- **S2:** Ship cruda v1, iterar en público y validar antes de polish. Validate antes de continuar: tras cada cambio UI sustantivo, frenar para verificar en browser.
- **S3:** Spanish-first MX context en todo lo user-facing. Labels, naming, copy. Mollo y MolloTV nunca son "Search/Home/Settings".
- **S4:** Routing de modelos por costo: GPT-4o-mini para tareas auxiliares (test prompts, etiquetar, transformar texto), GPT-4o para medio, Sonnet solo cuando complejo lo justifica. Nunca quemar Sonnet en código boilerplate.
- **S5:** Scope antes de barrer. Para diagnósticos amplios usar AskUserQuestion por dimensión, no multi-bash sweep. Para acciones destructivas, confirmar antes.
- **S6:** Memoria persistente en markdown plano (auto-memory + TELOS). Lees con `cat`, buscas con `rg`, versionas con `git`. Cero vector DBs para identity/goals — solo para semantic search de docs.
- **S7:** ROI counterfactual explícito: para reportar valor de un sistema, separar (a) lo que cambió, (b) qué hubiera pasado sin él, (c) cuál es el delta verificable.

## Notas

Estrategias son substrate-independent — siguen valiendo cuando cambias de stack o industria.
