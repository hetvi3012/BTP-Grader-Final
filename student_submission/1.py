def is_palindrome(text):
    # Student forgot to remove spaces and handle lowercase!
    reversed_text = text[::-1]
    if text == reversed_text:
        return True
    else:
        return False