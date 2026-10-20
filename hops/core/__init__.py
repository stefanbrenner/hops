"""Tk-free HOPS processing logic, used by the web interface (hops.web).

Each module mirrors one stage of the Tkinter application and keeps its
numerical behaviour, but takes plain values instead of widgets and reports
progress through a `JobContext` instead of Tk callbacks.
"""
