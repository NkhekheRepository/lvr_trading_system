# Contributing Guide

How to contribute to the LVR Trading System.

---

## Development Setup

### 1. Fork and Clone

```bash
# Fork on GitHub, then clone
git clone https://github.com/YOUR_USERNAME/lvr_trading_system.git
cd lvr_trading_system
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov
```

### 3. Create Feature Branch

```bash
git checkout -b feature/your-feature-name
```

---

## Code Style

### Python Style Guide

- Follow PEP 8
- Use type hints where possible
- Maximum line length: 100 characters
- Use meaningful variable names

### Docstring Format

```python
def function_name(param1: str, param2: int) -> bool:
    """
    Brief description of function.
    
    Longer description if needed, explaining the purpose,
    behavior, and any important details.
    
    Args:
        param1: Description of first parameter
        param2: Description of second parameter
    
    Returns:
        Description of return value
    
    Raises:
        ValueError: When this occurs
    
    Example:
        >>> result = function_name("test", 42)
        >>> print(result)
        True
    """
    pass
```

---

## Testing

### Run Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ -v --cov=. --cov-report=html

# Specific test
pytest tests/test_features.py -v
```

### Write Tests

```python
def test_your_feature():
    """Test description."""
    # Arrange
    input_data = ...
    
    # Act
    result = your_function(input_data)
    
    # Assert
    assert result == expected
```

### Test Requirements

- All new features must have tests
- Tests must pass on all supported Python versions
- Coverage must not decrease

---

## Commit Guidelines

### Commit Message Format

```
type(scope): short description

Longer description if needed, explaining what
was changed and why.

Fixes #123
```

### Types

| Type | Description |
|------|-------------|
| feat | New feature |
| fix | Bug fix |
| docs | Documentation |
| style | Formatting |
| refactor | Code restructuring |
| test | Adding tests |
| chore | Maintenance |

### Examples

```bash
git commit -m "feat(features): add volume-weighted features

Added VWAP and volume imbalance features to complement
existing price-based features. These provide additional
signal diversity for mean-reversion strategies.

Closes #45"
```

---

## Pull Request Process

### 1. Before Submitting

```bash
# Run all tests
pytest tests/ -v

# Check code style
flake8 app/ features/ strategy/

# Format code
black app/ features/ strategy/
```

### 2. Create Pull Request

1. Push to your fork
2. Open PR on GitHub
3. Fill out PR template
4. Link any related issues

### 3. PR Template

```markdown
## Description
Brief description of changes.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
Describe how changes were tested.

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Comments added for complex code
- [ ] Documentation updated
- [ ] Tests added/updated
- [ ] All tests pass
```

---

## Code Review

### What We Look For

1. **Correctness** - Does it work as intended?
2. **Design** - Is it well-structured?
3. **Tests** - Are they comprehensive?
4. **Documentation** - Is it clear?
5. **Performance** - Any concerns?

### Review Timeline

- Initial review: 2-3 business days
- Response to feedback: 1-2 business days
- Merge after approval

---

## Project Structure

```
lvr_trading_system/
├── app/              # Application core
│   ├── main.py       # Entry point
│   └── schemas.py    # Data models
├── features/         # Feature engineering
├── strategy/         # Trading strategy
├── execution/        # Execution engines
├── portfolio/        # Portfolio management
├── risk/            # Risk management
├── learning/        # Machine learning
├── monitoring/       # Monitoring
├── state/          # State management
├── data/           # Data loading
├── tests/          # Test suite
├── docs/           # Documentation
└── infrastructure/  # Deployment
```

---

## Questions?

- Open an issue for bugs
- Start a discussion for questions
- Check existing issues before creating new ones
