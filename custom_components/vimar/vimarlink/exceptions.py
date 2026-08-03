"""Vimar API Exception classes."""

from __future__ import annotations


class VimarApiError(Exception):
    """Vimar API General Exception."""

    def __init__(self, *args, **kwargs):
        """Init a default Vimar api exception."""
        self.err_args = args
        super().__init__(*args)

    def __str__(self):
        """Stringify exception text.

        The message is returned verbatim. It used to be run through percent
        formatting:

            return f"{...}: {self.err_args[0]}" % self.err_args[1:]

        which made stringifying the exception raise whenever the message
        contained a '%' and no matching argument - and messages routinely do,
        because requests echoes percent-encoded URLs back at us ("...url:
        /login.php?password=%26abc" -> TypeError: not enough arguments for
        format string). With no arguments at all it raised IndexError instead.

        That turned a plain connection failure into a crash inside the error
        handling itself: the config flow stringifies the exception to pick the
        message to show, and the coordinator interpolates it into UpdateFailed.
        Nothing in the codebase ever passed formatting arguments, so the
        formatting only ever had the power to break things.
        """
        if not self.err_args:
            return self.__class__.__name__
        return f"{self.__class__.__name__}: {self.err_args[0]}"


class VimarConfigError(VimarApiError):
    """Vimar API Configuration Exception."""


class VimarConnectionError(VimarApiError):
    """Vimar API Connection Exception."""
