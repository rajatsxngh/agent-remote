"""Entry point for the packaged Windows app (PyInstaller runs this)."""

from agent_remote.cli import main

raise SystemExit(main())
