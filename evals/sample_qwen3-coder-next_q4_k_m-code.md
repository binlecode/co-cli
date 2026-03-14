```python
from typing import List, Tuple, Set, Optional

def solve_n_queens(n: int) -> List[List[str]]:
    """
    Solve the N-Queens problem and return all valid board configurations.
    
    The N-Queens problem asks to place N chess queens on an N×N chessboard such that
    no two queens threaten each other. This means no two queens can share the same row,
    column, diagonal, or anti-diagonal.

    This implementation uses backtracking with optimized constraint checking via
    bitmasks and hash sets for O(1) lookups.

    Args:
        n (int): The size of the chessboard (n × n). Must be ≥ 0.
    
    Returns:
        List[List[str]]: A list of all valid board configurations. Each board is
                         represented as a list of strings, where 'Q' denotes a queen
                         and '.' denotes an empty square.
    
    Example:
        >>> solve_n_queens(4)
        [['.Q..', '...Q', 'Q...', '..Q.'],
         ['..Q.', 'Q...', '...Q', '.Q..']]
    
    Note:
        - For n = 0, returns [[]] (one valid empty board).
        - For n = 1, returns [['Q']].
        - For n = 2 or n = 3, returns [] (no solutions exist).
        - Time complexity: O(n!) in the worst case, but pruned effectively.
        - Space complexity: O(n^2) for storing solutions + O(n) for recursion stack.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    
    # Handle edge cases
    if n == 0:
        return [[""]]
    if n == 1:
        return [["Q"]]
    
    # For n = 2 or 3, no solutions exist — skip computation
    if n in (2, 3):
        return []
    
    # Initialize data structures for constraint tracking
    solutions: List[List[str]] = []
    
    # Track used columns (set for O(1) lookup)
    # queens_in_cols[col_idx] = True if a queen is placed in column col_idx
    queens_in_cols: Set[int] = set()
    
    # Track used diagonals (d1): row - col (constant for each diagonal)
    # d1 diagonal index ranges from -(n-1) to (n-1), so we offset by (n-1) to make indices non-negative
    used_d1: Set[int] = set()
    
    # Track used anti-diagonals (d2): row + col (constant for each anti-diagonal)
    # d2 diagonal index ranges from 0 to 2(n-1)
    used_d2: Set[int] = set()
    
    # Store the current placement: queens_in_rows[row] = col (the column where queen is placed in this row)
    # Using a list is more memory-efficient than a dict since rows are 0..n-1
    queens_placement: List[int] = [-1] * n  # Initialize with -1 (no queen placed yet)
    
    def backtrack(row: int) -> None:
        """
        Recursively place queens row by row using backtracking.
        
        Args:
            row (int): Current row to place a queen in (0-indexed).
        """
        # Base case: all n queens placed successfully
        if row == n:
            # Convert the placement list into a board representation
            board = []
            for r in range(n):
                # Build the row string: '.' in all positions except where the queen is
                row_str = '.' * queens_placement[r] + 'Q' + '.' * (n - queens_placement[r] - 1)
                board.append(row_str)
            solutions.append(board)
            return
        
        # Try placing a queen in each column of the current row
        for col in range(n):
            # Skip if column is already occupied
            if col in queens_in_cols:
                continue
            
            # Calculate diagonal indices
            d1 = row - col          # Main diagonal (top-left to bottom-right)
            d2 = row + col          # Anti-diagonal (top-right to bottom-left)
            
            # Skip if either diagonal is already occupied
            if d1 in used_d1 or d2 in used_d2:
                continue
            
            # Place the queen: update constraints
            queens_in_cols.add(col)
            used_d1.add(d1)
            used_d2.add(d2)
            queens_placement[row] = col
            
            # Recurse to the next row
            backtrack(row + 1)
            
            # Backtrack: remove the queen and restore constraints
            queens_in_cols.remove(col)
            used_d1.remove(d1)
            used_d2.remove(d2)
            queens_placement[row] = -1
    
    # Start backtracking from row 0
    backtrack(0)
    
    return solutions


def solve_n_queens_bitmask(n: int) -> List[List[str]]:
    """
    Alternative bitmask-optimized version of the N-Queens solver.
    
    Uses bitmasks instead of sets for diagonal and column tracking, which
    can be faster for larger n due to reduced memory overhead and bitwise
    operations being highly optimized in modern CPUs.

    Args:
        n (int): The size of the chessboard (n × n). Must be ≥ 0.
    
    Returns:
        List[List[str]]: A list of all valid board configurations (same format as solve_n_queens).
    
    Note:
        - This version supports n ≤ 32 (or n ≤ 64 on 64-bit systems) due to bit-width limits.
        - For n > 15, this version may be significantly faster than the set-based version.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return [[""]]
    if n == 1:
        return [["Q"]]
    if n in (2, 3):
        return []
    if n > 32:
        raise ValueError("Bitmask implementation only supports n ≤ 32 for portability")
    
    solutions: List[List[str]] = []
    queens_placement: List[int] = [-1] * n
    
    # Bitmasks: bit i set to 1 means column i (or diagonal i) is occupied
    # We'll use only the least significant n bits
    
    def backtrack(row: int, cols_mask: int, diag1_mask: int, diag2_mask: int) -> None:
        """
        Bitmask-based backtracking.
        
        Args:
            row (int): Current row.
            cols_mask (int): Bitmask of occupied columns.
            diag1_mask (int): Bitmask of occupied d1 diagonals (row - col + n - 1).
            diag2_mask (int): Bitmask of occupied d2 diagonals (row + col).
        """
        if row == n:
            # Convert placement to board representation
            board = []
            for r in range(n):
                row_str = '.' * queens_placement[r] + 'Q' + '.' * (n - queens_placement[r] - 1)
                board.append(row_str)
            solutions.append(board)
            return
        
        # All available columns: invert used columns mask and keep only n bits
        available_cols = ((1 << n) - 1) & ~cols_mask
        
        # Try each available column
        while available_cols:
            # Extract the rightmost available column (Least Significant Bit)
            col = available_cols & -available_cols  # isolates the LSB
            
            # Remove the chosen column from available_cols for next iterations
            available_cols ^= col
            
            # Convert bit position to integer column index
            col_idx = (col.bit_length() - 1)
            
            # Calculate diagonal masks
            d1 = row - col_idx + n - 1  # d1 index: 0 to 2n-2
            d2 = row + col_idx          # d2 index: 0 to 2n-2
            
            # Check if diagonals are safe
            # d1_bit and d2_bit are the corresponding bits for these diagonals
            d1_bit = 1 << d1
            d2_bit = 1 << d2
            
            if d1_bit & diag1_mask or d2_bit & diag2_mask:
                continue
            
            # Place queen: update masks and placement
            queens_placement[row] = col_idx
            
            backtrack(
                row + 1,
                cols_mask | col,
                diag1_mask | d1_bit,
                diag2_mask | d2_bit
            )
    
    backtrack(0, 0, 0, 0)
    return solutions


# Example usage and test (uncomment to run)
if __name__ == "__main__":
    # Test correctness for small n
    for n in range(1, 9):
        solutions = solve_n_queens(n)
        print(f"n={n}: {len(solutions)} solutions")
        if n <= 5:
            for i, sol in enumerate(solutions):
                print(f"Solution {i+1}:")
                for row in sol:
                    print(row)
                print()
    
    # Performance comparison (optional)
    # import time
    # for n in (8, 10, 12):
    #     t0 = time.time()
    #     sols1 = solve_n_queens(n)
    #     t1 = time.time()
    #     sols2 = solve_n_queens_bitmask(n)
    #     t2 = time.time()
    #     print(f"n={n}: set-based: {t1-t0:.3f}s, bitmask: {t2-t1:.3f}s, solutions={len(sols1)}")
```