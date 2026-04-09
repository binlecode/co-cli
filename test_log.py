import asyncio
import logging

from evals.eval_bootstrap_flow import main

logging.basicConfig(level=logging.DEBUG)
asyncio.run(main())
