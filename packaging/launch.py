"""Entry point for the packaged Windows app (PyInstaller runs this)."""

from ps5_agent_handler.cli import main

raise SystemExit(main())
