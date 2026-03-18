def is_palindrome(text):
    reversed_text = text[::-1]
    if text == reversed_text:
        return True
    else:
        return False