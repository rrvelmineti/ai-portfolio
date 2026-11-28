"""Synthetic claims extracts for 10 fictional health-plan clients, with answer keys (ADR 0010).

- ``dictionary``: the target data dictionary (13 fields and their rules).
- ``rules``: the rule checks, with the same semantics as the original app's engine.
- ``formats``: reading and writing CSV / pipe / tab / .xlsx, encodings, preambles, footers.
- ``profiles``: how each fictional client's extract differs.
- ``generate``: builds every file, its mapping key and error key, and the manifest.
- ``check``: the two-way checker (every keyed error is real; nothing else is wrong).
- ``seed``: loads the dictionary, clients and week-1 file metadata into the demo tenant.

Every value is invented. Procedure codes are synthetic (``Z`` + 4 digits).
"""
