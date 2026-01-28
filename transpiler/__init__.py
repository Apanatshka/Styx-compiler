"""
Styx Transpiler Package

A transpiler for converting synchronous code with @entity decorators
into asynchronous stateful functions.
"""

from .core import StyxTranspiler

__all__ = ['StyxTranspiler']