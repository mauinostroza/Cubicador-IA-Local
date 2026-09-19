import unittest

from cubicador.runtime import _worker_environment
from cubicador.security import SecurityPolicy, intel_8gb_policy


class CpuProfileTests(unittest.TestCase):
    def test_i3_profile_is_two_threads_one_job_two_gib(self):
        policy = intel_8gb_policy("i3")
        self.assertEqual(policy.model_threads, 2)
        self.assertEqual(policy.max_concurrent_jobs, 1)
        self.assertEqual(policy.max_queued_jobs, 1)
        self.assertEqual(policy.windows_job_memory_bytes, 2 * 1024**3)
        self.assertEqual(_worker_environment("C:/vendor", policy)["PADDLE_NUM_THREADS"], "2")

    def test_i5_profile_allows_four_threads_or_lower(self):
        policy = intel_8gb_policy("i5", model_threads=3)
        self.assertEqual(policy.model_threads, 3)
        self.assertEqual(_worker_environment("C:/vendor", policy)["OMP_NUM_THREADS"], "3")

    def test_profile_cannot_raise_hardware_limits(self):
        with self.assertRaises(ValueError):
            intel_8gb_policy("i3", model_threads=3)
        with self.assertRaises(ValueError):
            intel_8gb_policy("i5", windows_job_memory_bytes=2 * 1024**3 + 1)
        with self.assertRaises(ValueError):
            intel_8gb_policy("i3", max_concurrent_jobs=2)
        with self.assertRaises(ValueError):
            intel_8gb_policy("i3", max_queued_jobs=2)

    def test_invalid_processor_rejected(self):
        with self.assertRaises(ValueError):
            intel_8gb_policy("i7")


if __name__ == "__main__":
    unittest.main()
