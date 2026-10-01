def average(numbers):
    """Return the mean of a non-empty list of numbers."""
    if not numbers:
        raise ValueError("average() needs at least one number")
    return sum(numbers) / len(numbers)
