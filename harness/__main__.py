"""`python -m harness`: the same as `me`. Windows scheduled tasks run it under pythonw."""

import sys

from .cli import main

sys.exit(main())
