"""
Gate agent: watches a gate camera, asks the LPR service for a decision when a
vehicle stops at the barrier, and relays open commands to the gate controller
(ESP32, or the simulated barrier served by the LPR service).
"""

__version__ = '1.0.0'
