"""Module entry point for the Shelly integration."""

import asyncio

from uc_intg_shelly import main

if __name__ == "__main__":
    asyncio.run(main())
