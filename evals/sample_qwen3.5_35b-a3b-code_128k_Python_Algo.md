# Prompt

Write a highly optimized Python function to solve the N-Queens problem. Include detailed type hints, docstrings, and comments explaining the backtracking logic.

# Response

```python
"""
N-Queens Problem Solver

This module provides a highly optimized solution to the N-Queens problem using
backtracking with efficient constraint checking via sets.

The N-Queens problem asks: In how many ways can N queens be placed on an N×N
chessboard such that no two queens attack each other?

A queen attacks all cells in its row, column, and both diagonals.
"""

from typing import List, Tuple, Set, Optional, Generator


def n_queens_solutions(n: int) -> List[List[str]]:
    """
    Generate all valid solutions to the N-Queens problem.

    Each solution is represented as a list of strings, where each string
    corresponds to a row on the board. 'Q' represents a queen and '.'
    represents an empty cell.

    Args:
        n (int): The size of the board and the number of queens.

    Returns:
        List[List[str]]: A list of all valid board configurations.

    Raises:
        ValueError: If n is less than 1.

    Example:
        >>> solutions = n_queens_solutions(4)
        >>> len(solutions)
        2
        >>> solutions[0]
        [".Q..", "...Q", "Q...", "..Q."]
    """
    if n < 1:
        raise ValueError("Board size must be at least 1")

    solutions: List[List[str]] = []
    cols: Set[int] = set()
    pos_diag: Set[int] = set()  # row + col
    neg_diag: Set[int] = set()  # row - col
    board: List[int] = [-1] * n  # board[row] = col

    def backtrack(row: int) -> None:
        """
        Recursively place queens row by row using backtracking.

        Args:
            row (int): The current row index to place a queen.
        """
        if row == n:
            # All queens placed successfully; construct board representation
            solutions.append(["." * col + "Q" + "." * (n - col - 1) for col in board])
            return

        for col in range(n):
            if col in cols or (row + col) in pos_diag or (row - col) in neg_diag:
                continue

            # Place queen
            cols.add(col)
            pos_diag.add(row + col)
            neg_diag.add(row - col)
            board[row] = col

            backtrack(row + 1)

            # Backtrack
            cols.remove(col)
            pos_diag.remove(row + col)
            neg_diag.remove(row - col)
            board[row] = -1

    backtrack(0)
    return solutions


def count_n_queens(n: int) -> int:
    """
    Count the number of valid N-Queens solutions without returning configurations.

    This version is optimized for performance when only the number of solutions
    is needed.

    Args:
        n (int): The size of the board and the number of queens.

    Returns:
        int: The total number of valid solutions.

    Raises:
        ValueError: If n is less than 1.

    Example:
        >>> count_n_queens(4)
        2
    """
    if n < 1:
        raise ValueError("Board size must be at least 1")

    count = 0
    cols: Set[int] = set()
    pos_diag: Set[int] = set()
    neg_diag: Set[int] = set()

    def backtrack(row: int) -> None:
        nonlocal count
        if row == n:
            count += 1
            return

        for col in range(n):
            if col in cols or (row + col) in pos_diag or (row - col) in neg_diag:
                continue

            cols.add(col)
            pos_diag.add(row + col)
            neg_diag.add(row - col)

            backtrack(row + 1)

            cols.remove(col)
            pos_diag.remove(row + col)
            neg_diag.remove(row - col)

    backtrack(0)
    return count


def n_queens_with_bitmask(n: int) -> List[List[str]]:
    """
    Generate all N-Queens solutions using bitwise operations for maximum performance.

    This version uses bitwise operations to track occupied columns and diagonals,
    enabling faster conflict checks and potentially improved performance for large n.

    Args:
        n (int): The size of the board and the number of queens.

    Returns:
        List[List[str]]: A list of all valid board configurations.

    Raises:
        ValueError: If n is less than 1.
    """
    if n < 1:
        raise ValueError("Board size must be at least 1")

    solutions: List[List[str]] = []
    board: List[int] = [0] * n

    def backtrack(row: int, cols: int, pos_diag: int, neg_diag: int) -> None:
        if row == n:
            solutions.append(["." * col + "Q" + "." * (n - col - 1) for col in board])
            return

        available = ((1 << n) - 1) & ~(cols | pos_diag | neg_diag)
        while available:
            col = available & -available
            col_idx = col.bit_length() - 1
            board[row] = col_idx
            backtrack(
                row + 1,
                cols | col,
                (pos_diag | col) << 1,
                (neg_diag | col) >> 1
            )
            available &= available - 1

    backtrack(0, 0, 0, 0)
    return solutions


def validate_solution(board: List[List[str]]) -> bool:
    """
    Validate whether a given board configuration is a valid N-Queens solution.

    Args:
        board (List[List[str]]): A list of strings representing the board.

    Returns:
        bool: True if the board is valid, False otherwise.
    """
    n = len(board)
    if n == 0:
        return True

    cols: Set[int] = set()
    pos_diag: Set[int] = set()
    neg_diag: Set[int] = set()

    for row in range(n):
        if len(board[row]) != n:
            return False
        queen_col = board[row].find("Q")
        if queen_col == -1:
            return False
        if queen_col in cols or (row + queen_col) in pos_diag or (row - queen_col) in neg_diag:
            return False
        cols.add(queen_col)
        pos_diag.add(row + queen_col)
        neg_diag.add(row - queen_col)

    return True


def generate_solutions(n: int, max_solutions: Optional[int] = None) -> Generator[List[str], None, None]:
    """
    Generator that yields N-Queens solutions one at a time.

    Useful for memory efficiency when dealing with large N or when only
    a subset of solutions is needed.

    Args:
        n (int): Board size.
        max_solutions (Optional[int]): Maximum number of solutions to yield.

    Yields:
        List[str]: Each solution as a list of strings representing the board.
    """
    if n < 1:
        raise ValueError("Board size must be at least 1")

    solutions = 0
    cols: Set[int] = set()
    pos_diag: Set[int] = set()
    neg_diag: Set[int] = set()
    board: List[int] = [-1] * n

    def backtrack(row: int) -> None:
        nonlocal solutions
        if max_solutions and solutions >= max_solutions:
            return

        if row == n:
            yield ["." * col + "Q" + "." * (n - col - 1) for col in board]
            solutions += 1
            return

        for col in range(n):
            if col in cols or (row + col) in pos_diag or (row - col) in neg_diag:
                continue

            cols.add(col)
            pos_diag.add(row + col)
            neg_diag.add(row - col)
            board[row] = col

            yield from backtrack(row + 1)

            cols.remove(col)
            pos_diag.remove(row + col)
            neg_diag.remove(row - col)
            board[row] = -1

    yield from backtrack(0)


if __name__ == "__main__":
    # Example usage and demonstration
    for size in range(1, 9):
        solutions = n_queens_solutions(size)
        print(f"Size {size}: {len(solutions)} solution(s)")
        if solutions:
            print(f"First solution:\n{'\n'.join(solutions[0])}\n")
```