import math
import tkinter as tk
from tkinter import messagebox

# --- MODEL (Logic & AI) ---
class TicTacToeAI:
    def __init__(self):
        self.board = [[' ' for _ in range(3)] for _ in range(3)]
        self.current_player = 'X'  # Human is X, AI is O

    def make_move(self, row, col):
        if 0 <= row < 3 and 0 <= col < 3 and self.board[row][col] == ' ':
            self.board[row][col] = self.current_player
            return True
        return False

    def check_winner(self, player=None):
        p = player if player else self.current_player
        for i in range(3):
            if all(self.board[i][j] == p for j in range(3)) or \
               all(self.board[j][i] == p for j in range(3)):
                return True
        if all(self.board[i][i] == p for i in range(3)) or \
           all(self.board[i][2-i] == p for i in range(3)):
            return True
        return False

    def is_full(self):
        return all(cell != ' ' for row in self.board for cell in row)

    def minimax(self, depth, is_maximizing):
        if self.check_winner('O'): return 10 - depth
        if self.check_winner('X'): return depth - 10
        if self.is_full(): return 0

        if is_maximizing:
            best_score = -math.inf
            for r in range(3):
                for c in range(3):
                    if self.board[r][c] == ' ':
                        self.board[r][c] = 'O'
                        score = self.minimax(depth + 1, False)
                        self.board[r][c] = ' '
                        best_score = max(score, best_score)
            return best_score
        else:
            best_score = math.inf
            for r in range(3):
                for c in range(3):
                    if self.board[r][c] == ' ':
                        self.board[r][c] = 'X'
                        score = self.minimax(depth + 1, True)
                        self.board[r][c] = ' '
                        best_score = min(score, best_score)
            return best_score

    def get_best_move(self):
        best_score = -math.inf
        move = None
        for r in range(3):
            for c in range(3):
                if self.board[r][c] == ' ':
                    self.board[r][c] = 'O'
                    score = self.minimax(0, False)
                    self.board[r][c] = ' '
                    if score > best_score:
                        best_score = score
                        move = (r, c)
        return move

# --- VIEW & CONTROLLER (GUI) ---
class TicTacToeGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Pro Tic-Tac-Toe AI v1.0")
        self.game = TicTacToeAI()
        self.buttons = [[None for _ in range(3)] for _ in range(3)]
        self.setup_ui()

    def setup_ui(self):
        for r in range(3):
            for c in range(3):
                self.buttons[r][c] = tk.Button(
                    self.root, text="", font=('Arial', 24, 'bold'),
                    width=5, height=2, bg="#f0f0f0",
                    command=lambda row=r, col=c: self.handle_human_move(row, col)
                )
                self.buttons[r][c].grid(row=r, column=c, sticky="nsew")

    def handle_human_move(self, r, c):
        if self.game.board[r][c] == ' ' and self.game.current_player == 'X':
            self.update_cell(r, c, 'X', "#3498db")
            if not self.check_end():
                self.game.current_player = 'O'
                self.root.after(500, self.handle_ai_move)

    def handle_ai_move(self):
        move = self.game.get_best_move()
        if move:
            self.update_cell(move[0], move[1], 'O', "#e74c3c")
            if not self.check_end():
                self.game.current_player = 'X'

    def update_cell(self, r, c, player, color):
        self.game.board[r][c] = player
        self.buttons[r][c].config(text=player, fg=color)

    def check_end(self):
        if self.game.check_winner():
            messagebox.showinfo("Game Over", f"Player {self.game.current_player} wins!")
            self.reset()
            return True
        if self.game.is_full():
            messagebox.showinfo("Game Over", "It's a draw!")
            self.reset()
            return True
        return False

    def reset(self):
        self.game = TicTacToeAI()
        for r in range(3):
            for c in range(3):
                self.buttons[r][c].config(text="", fg="black")

if __name__ == "__main__":
    root = tk.Tk()
    app = TicTacToeGUI(root)
    root.mainloop()