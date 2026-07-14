#!/usr/bin/env python3
"""Convenience launcher: python run.py"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("clipfarm.server:app", host="127.0.0.1", port=8000)
