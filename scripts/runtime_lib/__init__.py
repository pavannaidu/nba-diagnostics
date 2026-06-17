"""Runtime asset helpers used by DAB entrypoints.

The implementation currently delegates to ``scripts.runtime_assets`` so existing
imports remain compatible while the entrypoints move to a package-shaped API.
"""

from runtime_assets import *  # noqa: F401,F403
