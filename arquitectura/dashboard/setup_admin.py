#!/usr/bin/env python3
# Copyright 2026 Mauro Javier Silva
# SPDX-License-Identifier: Apache-2.0
"""setup_admin.py — initial admin credential for the dashboard, for the installer.

Rules (safe for reinstallation):
  - If the admin does not exist: creates it and returns "<username> <password>" (shown ONCE).
  - If it exists with `must_change_password=1` (never changed the initial one): rotates the
    password and returns the new one.
  - If it exists with `must_change_password=0` (already changed it): does NOT rotate it; returns
    "<username> __YA_CONFIGURADA__" so as not to overwrite a password the human already chose.

Runs as the dashboard user (charasky) to avoid breaking auth.db ownership.

Usage:  sudo -u charasky python3 setup_admin.py
"""
import auth


def main():
    auth.init()  # creates the schema + admin if it does not exist
    u = auth.user("admin")
    if u is None or u.get("must_change_password"):
        username, password = auth.reset_admin_password()
        print(f"{username} {password}")
        return
    print("admin __YA_CONFIGURADA__")


if __name__ == "__main__":
    main()
