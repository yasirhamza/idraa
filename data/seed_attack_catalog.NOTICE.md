# MITRE ATT&CK® catalog attribution

`data/seed_attack_catalog.json` is derived from the MITRE ATT&CK® knowledge
base (Enterprise and ICS matrices).

> © 2026 The MITRE Corporation. This work is reproduced and distributed with the permission of The MITRE Corporation.

ATT&CK® is a registered trademark of The MITRE Corporation.

- Source: https://github.com/mitre-attack/attack-stix-data (official STIX 2.1
  bundles), Terms of Use: https://attack.mitre.org/resources/legal-and-branding/terms-of-use/
- ATT&CK release: 19.1
- Source commit: a6c366439edee3a87b79cf90dc0b93f5d7975956
- Accessed: 2026-07-04
- Transformations: techniques only (sub-techniques excluded), revoked/deprecated
  objects excluded, descriptions trimmed to the first paragraph with
  `(Citation: ...)` markers stripped. Built by `scripts/build_attack_catalog_seed.py`.

Idraa's scenario→technique mappings (`library_entry_attack_mappings`,
`scenario_attack_mappings`) are Idraa curation/authoring artifacts, NOT
MITRE content.
