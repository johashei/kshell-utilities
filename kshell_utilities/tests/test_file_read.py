import sys
sys.path.insert(0, "../")
from recreate_fig_6 import ReadKshellOutput

def _test_file_read():
    """
    Test that 'read_kshell_output' successfully reads output from kshell.

    Raises
    ------
    AssertionError
        If the read values are not exactly equal to the expected values.
    """
    res = ReadKshellOutput()
    E_x, B_M1 = res.read_kshell_output("test_text_file.txt")
    E_x_expected = [0.0, 8.016]
    B_M1_expected = [
        [5.172, 20.5, 5.172],
        [17.791, 0.0, 17.791],
        [19.408, 5.7, 1.617],
        [18.393, 0.1, 0.602]
    ]
    
    for calculated, expected in zip(E_x, E_x_expected):
        msg = f"Error in E_x. Expected: {expected}, got: {calculated}."
        assert calculated == expected, msg

    for calculated, expected in zip(B_M1, B_M1_expected):
        msg = f"Error in B_M1. Expected: {expected}, got: {calculated}."
        success = (calculated[0] == expected[0]) and (calculated[1] == expected[1]) and (calculated[2] == expected[2])
        assert success, msg

if __name__ == "__main__":
    _test_file_read()