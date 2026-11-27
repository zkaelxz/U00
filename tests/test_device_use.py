from lib import device_use


def test_the_counts_are_reported_once_and_then_forgotten():
    device_use.take()
    device_use.note("text reader (medium)", "GPU")
    device_use.note("text reader (medium)", "GPU")
    device_use.note("eraser (LaMa)", "CPU")
    assert device_use.take() == "eraser (LaMa) CPU x1, text reader (medium) GPU x2"
    assert device_use.take() == ""
