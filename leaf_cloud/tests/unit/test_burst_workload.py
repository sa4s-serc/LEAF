from leaf_cloud.core.workload import BurstWorkload

def test_burst_timing():
    # Create a workload with 5s bursts every 30s
    workload = BurstWorkload(
        base_rate=5.0,
        burst_rate=50.0,
        burst_duration=5.0,
        burst_interval=30.0
    )
    
    # Test within base period (0-25s, 35-55s, etc.)
    assert workload.get_rate_at_time(0.0) == 5.0, "Expected base rate at 0.0s"
    assert workload.get_rate_at_time(10.0) == 5.0, "Expected base rate at 10.0s"
    assert workload.get_rate_at_time(24.999) == 5.0, "Expected base rate at 24.999s"
    
    # Test at burst start (included in burst period)
    assert workload.get_rate_at_time(25.0) == 50.0, "Expected burst rate at 25.0s"
    
    # Test within burst period (25-30s, 55-60s, etc.)
    assert workload.get_rate_at_time(25.1) == 50.0, "Expected burst rate at 25.1s"
    assert workload.get_rate_at_time(27.5) == 50.0, "Expected burst rate at 27.5s"
    assert workload.get_rate_at_time(29.999) == 50.0, "Expected burst rate at 29.999s"
    
    # Test at burst end (exclusive of 30.0s)
    assert workload.get_rate_at_time(30.0) == 5.0, "Expected base rate at 30.0s"
    
    # Test next cycle
    assert workload.get_rate_at_time(35.0) == 5.0, "Expected base rate at 35.0s"
    assert workload.get_rate_at_time(54.999) == 5.0, "Expected base rate at 54.999s"
    
    # Test at next burst start (included in burst period)
    assert workload.get_rate_at_time(55.0) == 50.0, "Expected burst rate at 55.0s"
    
    # Test within next burst period
    assert workload.get_rate_at_time(55.1) == 50.0, "Expected burst rate at 55.1s"
    assert workload.get_rate_at_time(59.999) == 50.0, "Expected burst rate at 59.999s"
    
    # Test at next cycle start
    assert workload.get_rate_at_time(60.0) == 5.0, "Expected base rate at 60.0s"
    
    print("All tests passed!")

if __name__ == "__main__":
    test_burst_timing()
