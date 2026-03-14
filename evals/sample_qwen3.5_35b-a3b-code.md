```python
"""
N-Queens Problem Solver

This module provides a highly optimized solution to the N-Queens problem using
backtracking with bit manipulation for constraint checking.

The N-Queens problem requires placing N queens on an N×N chessboard such that
no two queens attack each other. This means no two queens can share the same
row, column, or diagonal.
"""

from typing import List, Set, Tuple, Generator
import sys

# Increase recursion depth for larger boards (default is 1000)
sys.setrecursionlimit(10000)


def n_queens(n: int) -> List[List[int]]:
    """
    Solve the N-Queens problem and return all valid board configurations.

    Args:
        n: The number of queens and board size (N×N).

    Returns:
        A list of solutions, where each solution is represented as a list of
        column indices (1-indexed) for each row.

    Raises:
        ValueError: If n is less than 1 or greater than 15.

    Example:
        >>> solutions = n_queens(4)
        >>> len(solutions)
        2
    """
    if not isinstance(n, int) or n < 1:
        raise ValueError("Board size must be a positive integer")
    if n > 15:
        raise ValueError("Board size exceeds practical computation limits")

    solutions: List[List[int]] = []
    cols: Set[int] = set()
    diag1: Set[int] = set()  # Row + Col
    diag2: Set[int] = set()  # Row - Col

    def backtrack(row: int, current: List[int]) -> None:
        """
        Recursively attempt to place queens row by row.

        Args:
            row: Current row being considered (0-indexed).
            current: Column positions of queens placed so far.
        """
        if row == n:
            solutions.append(current[:])
            return

        for col in range(n):
            d1, d2 = row + col, row - col
            if col in cols or d1 in diag1 or d2 in diag2:
                continue

            cols.add(col)
            diag1.add(d1)
            diag2.add(d2)
            current.append(col)
            backtrack(row + 1, current)
            current.pop()
            cols.remove(col)
            diag1.remove(d1)
            diag2.remove(d2)

    backtrack(0, [])
    return solutions


def n_queens_bitwise(n: int) -> List[List[int]]:
    """
    Solve the N-Queens problem using bit manipulation for maximum performance.

    This version uses bitwise operations to track column and diagonal constraints,
    significantly improving performance for larger boards.

    Args:
        n: The number of queens and board size (N×N).

    Returns:
        A list of solutions, where each solution is represented as a list of
        column indices (0-indexed) for each row.

    Raises:
        ValueError: If n is less than 1 or greater than 15.
    """
    if not isinstance(n, int) or n < 1:
        raise ValueError("Board size must be a positive integer")
    if n > 15:
        raise ValueError("Board size exceeds practical computation limits")

    solutions: List[List[int]] = []

    def backtrack(
        row: int,
        cols: int,
        diag1: int,
        diag2: int,
        current: List[int]
    ) -> None:
        """
        Backtracking with bitwise constraint tracking.

        Args:
            row: Current row index.
            cols: Bitmask of occupied columns.
            diag1: Bitmask of occupied diagonals (row + col).
            diag2: Bitmask of occupied diagonals (row - col).
            current: Column indices of placed queens.
        """
        if row == n:
            solutions.append(current[:])
            return

        available = ((1 << n) - 1) & ~(cols | diag1 | diag2)
        while available:
            col_bit = available & -available  # Extract lowest set bit
            col = col_bit.bit_length() - 1
            current.append(col)
            backtrack(
                row + 1,
                cols | col_bit,
                (diag1 | col_bit) << 1,
                (diag2 | col_bit) >> 1,
                current
            )
            current.pop()
            available &= available - 1  # Clear lowest set bit

    backtrack(0, 0, 0, 0, [])
    return solutions


def n_queens_generator(n: int) -> Generator[List[int], None, None]:
    """
    Generator version of the N-Queens solver for memory efficiency.

    Yields solutions one at a time instead of storing all in memory.

    Args:
        n: The number of queens and board size (N×N).

    Yields:
        Each solution as a list of column indices (0-indexed).

    Example:
        >>> for sol in n_queens_generator(4):
        ...     print(sol)
        [1, 3, 0, 2]
        [2, 0, 3, 1]
    """
    if not isinstance(n, int) or n < 1:
        raise ValueError("Board size must be a positive integer")

    cols: Set[int] = set()
    diag1: Set[int] = set()
    diag2: Set[int] = set()

    def backtrack(row: int, current: List[int]) -> Generator[List[int], None, None]:
        if row == n:
            yield current[:]
            return

        for col in range(n):
            d1, d2 = row + col, row - col
            if col in cols or d1 in diag1 or d2 in diag2:
                continue

            cols.add(col)
            diag1.add(d1)
            diag2.add(d2)
            current.append(col)
            yield from backtrack(row + 1, current)
            current.pop()
            cols.remove(col)
            diag1.remove(d1)
            diag2.remove(d2)

    yield from backtrack(0, [])


def print_board(n: int, solution: List[int]) -> str:
    """
    Convert a solution to a human-readable board representation.

    Args:
        n: Board size.
        solution: Column indices for each row.

    Returns:
        A string representation of the board.
    """
    lines = []
    for row in range(n):
        line = []
        for col in range(n):
            line.append("Q" if solution[row] == col else ".")
        lines.append(" ".join(line))
    return "\n".join(lines)


def count_solutions(n: int) -> int:
    """
    Count the number of valid N-Queens solutions without storing them.

    Args:
        n: Board size.

    Returns:
        Number of valid solutions.
    """
    count = 0
    cols: Set[int] = set()
    diag1: Set[int] = set()
    diag2: Set[int] = set()

    def backtrack(row: int) -> None:
        nonlocal count
        if row == n:
            count += 1
            return

        for col in range(n):
            d1, d2 = row + col, row - col
            if col in cols or d1 in diag1 or d2 in diag2:
                continue

            cols.add(col)
            diag1.add(d1)
            diag2.add(d2)
            backtrack(row + 1)
            cols.remove(col)
            diag1.remove(d1)
            diag2.remove(d2)

    backtrack(0)
    return count


# Example usage and test cases
if __name__ == "__main__":
    test_cases = [4, 5, 6, 8]
    for n in test_cases:
        print(f"\nN = {n}")
        solutions = n_queens(n)
        print(f"Number of solutions: {len(solutions)}")
        if solutions:
            print("First solution:")
            print(print_board(n, solutions[0]))
```