"""Run isolated purchase/delivery tests; never call the live payment gateway."""
import unittest

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromName('test_checkout')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
