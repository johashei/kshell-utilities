import os, sys, multiprocessing, hashlib, ast, time
from fractions import Fraction
from typing import Union, Callable
import numpy as np
import matplotlib.pyplot as plt
from .kshell_exceptions import KshellDataStructureError
from .general_utilities import level_plot, level_density, gamma_strength_function_average, porter_thomas
from .parameters import atomic_numbers, flags
from .loaders import _generic_loader, _load_energy_levels, _load_transition_probabilities, _load_transition_probabilities_old, _load_transition_probabilities_jem

def _generate_unique_identifier(path: str) -> str:
    """
    Generate a unique identifier based on the shell script and the
    save_input file from KSHELL.

    Parameters
    ----------
    path : str
        The path to a summary file or a directory with a summary file.
    """
    shell_file_content = ""
    save_input_content = ""
    msg = "Not able to generate unique identifier!"
    if os.path.isfile(path):
        """
        If a file is specified, extract the directory from the path.
        """
        directory = path.rsplit("/", 1)[0]
        if directory == path:
            """
            Example: path is 'summary.txt'
            """
            directory = "."

        for elem in os.listdir(directory):
            """
            Loop over all elements in the directory and find the shell
            script and save_input file.
            """
            if elem.endswith(".sh"):
                with open(f"{directory}/{elem}", "r") as infile:
                    shell_file_content += infile.read()
            # elif elem.endswith(".input"):
            elif "save_input_ui.txt" in elem:
                with open(f"{directory}/{elem}", "r") as infile:
                    save_input_content += infile.read()
    else:
        print(msg)

    if (shell_file_content == "") and (save_input_content == ""):
        print(msg)

    return hashlib.sha1((shell_file_content + save_input_content).encode()).hexdigest()

class ReadKshellOutput:
    """
    Read `KSHELL` data files and store the values as instance
    attributes.

    Attributes
    ----------
    levels : np.ndarray
        Array containing energy, spin, and parity for each excited
        state. [[E, 2*spin, parity, idx], ...]. idx counts how many
        times a state of that given spin and parity has occurred. The
        first 0+ state will have an idx of 1, the second 0+ will have an
        idx of 2, etc.

    transitions_BE1 : np.ndarray
        Transition data for BE1 transitions. Structure:
        NEW:
        [2*spin_initial, parity_initial, idx_initial, Ex_initial,
        2*spin_final, parity_final, idx_final, Ex_final, E_gamma,
        B(.., i->f), B(.., f<-i)]
        OLD NEW:
        [2*spin_initial, parity_initial, Ex_initial, 2*spin_final,
        parity_final, Ex_final, E_gamma, B(.., i->f), B(.., f<-i)]
        OLD:
        Mx8 array containing [2*spin_final, parity_initial, Ex_final,
        2*spin_initial, parity_initial, Ex_initial, E_gamma, B(.., i->f)].

    transitions_BM1 : np.ndarray
        Transition data for BM1 transitions. Same structure as BE1.

    transitions_BE2 : np.ndarray
        Transition data for BE2 transitions. Same structure as BE1.
    """
    def __init__(self, path: str, load_and_save_to_file: bool, old_or_new: str):
        """
        Parameters
        ----------
        path : string
            Path of `KSHELL` output file directory, or path to a
            specific `KSHELL` data file.

        load_and_save_to_file : bool
            Toggle saving data as `.npy` files on / off. If `overwrite`,
            saved `.npy` files are overwritten.

        old_or_new : str
            Choose between old and new summary file syntax. All summary
            files generated pre 2021-11-24 use old style.
            New:
            J_i  pi_i idx_i Ex_i    J_f  pi_f idx_f Ex_f      dE         B(E2)->         B(E2)->[wu]     B(E2)<-         B(E2)<-[wu]
            5    +    1     0.036   6    +    1     0.000     0.036     70.43477980      6.43689168     59.59865983      5.44660066
            Old:
            J_i    Ex_i     J_f    Ex_f   dE        B(M1)->         B(M1)<- 
            2+(11) 18.393 2+(10) 17.791 0.602 0.1(  0.0) 0.1( 0.0)
        """

        self.path = path
        self.load_and_save_to_file = load_and_save_to_file
        self.old_or_new = old_or_new
        # Some attributes might not be altered, depending on the input file.
        self.fname_summary = None
        self.fname_ptn = None
        self.nucleus = None
        self.model_space = None
        self.proton_partition = None
        self.neutron_partition = None
        self.levels = None
        self.transitions_BM1 = None
        self.transitions_BE2 = None
        self.transitions_BE1 = None
        self.truncation = None
        # Debug.
        self.negative_spin_counts = np.array([0, 0, 0, 0])  # The number of skipped -1 spin states for [levels, BM1, BE2, BE1].

        if isinstance(self.load_and_save_to_file, str) and (self.load_and_save_to_file != "overwrite"):
            msg = "Allowed values for 'load_and_save_to_file' are: 'True', 'False', 'overwrite'."
            msg += f" Got '{self.load_and_save_to_file}'."
            raise ValueError(msg)

        if os.path.isdir(path):
            """
            If input 'path' is a directory containing KSHELL files,
            extract info from both summary and .ptn file.
            """
            for elem in os.listdir(path):
                if elem.startswith("summary"):
                    self.fname_summary = f"{path}/{elem}"
                    self._extract_info_from_summary_fname()
                    self._read_summary()

                elif elem.endswith(".ptn"):
                    self.fname_ptn = f"{path}/{elem}"
                    self._extract_info_from_ptn_fname()
                    self.read_ptn()

        else:
            """
            'path' is a single file, not a directory.
            """
            fname = path.split("/")[-1]

            if fname.startswith("summary"):
                self.fname_summary = path
                self._extract_info_from_summary_fname()
                self._read_summary()

            elif fname.endswith(".ptn"):
                self.fname_ptn = path
                self._extract_info_from_ptn_fname()
                self._read_ptn()

            else:
                msg = f"Handling for file {fname} is not implemented."
                raise KshellDataStructureError(msg)

    def _extract_info_from_ptn_fname(self):
        """
        Extract nucleus and model space name.
        """
        fname_split = self.fname_ptn.split("/")[-1]
        fname_split = fname_split.split("_")
        self.nucleus = fname_split[0]
        self.model_space = fname_split[1]

    def _read_ptn(self):
        """
        Read `KSHELL` partition file (.ptn) and extract proton
        partition, neutron partition, and particle-hole truncation data.
        Save as instance attributes.
        """

        line_number = 0
        line_number_inner = 0
        self.truncation = []

        with open(self.fname_ptn, "r") as infile:
            for line in infile:
                line_number += 1
                
                if line.startswith("# proton partition"):
                    for line_inner in infile:
                        """
                        Read until next '#'.
                        """
                        line_number_inner += 1
                        if line_inner.startswith("#"):
                            line = line_inner
                            break
                    
                    self.proton_partition = np.loadtxt(
                        fname = self.fname_ptn,
                        skiprows = line_number,
                        max_rows = line_number_inner
                    )
                    line_number += line_number_inner
                    line_number_inner = 0
                
                if line.startswith("# neutron partition"):
                    for line_inner in infile:
                        """
                        Read until next '#'.
                        """
                        line_number_inner += 1
                        if line_inner.startswith("#"):
                            line = line_inner
                            break
                    
                    self.neutron_partition = np.loadtxt(
                        fname = self.fname_ptn,
                        skiprows = line_number,
                        max_rows = line_number_inner
                    )
                    line_number += line_number_inner
                    line_number_inner = 0

                if line.startswith("# particle-hole truncation"):
                    for line_inner in infile:
                        """
                        Loop over all particle-hole truncation lines.
                        """
                        line_number += 1
                        line_inner_split = line_inner.split()

                        if (len(line_inner_split) < 2):
                            """
                            Condition will probably not get fulfilled.
                            Safety precaution due to indexing in this
                            loop.
                            """
                            break

                        if (line_inner_split[1]).startswith("["):
                            """
                            '[' indicates that 'line_inner' is still
                            containing truncation information.
                            """
                            for colon_index, elem in enumerate(line_inner_split):
                                """
                                Find the index of the colon ':' to
                                decide the orbit numbers and occupation
                                numbers.
                                """
                                if (elem == ":"): break

                            occupation = [int(occ) for occ in line_inner_split[colon_index + 1:]]   # [min, max].
                            orbit_numbers = "".join(line_inner_split[1:colon_index])
                            orbit_numbers = orbit_numbers.replace("[", "")
                            orbit_numbers = orbit_numbers.replace("]", "")
                            orbit_numbers = orbit_numbers.replace(" ", "")  # This can prob. be removed because of the earlier split.
                            orbit_numbers = orbit_numbers.split(",")
                            orbit_numbers = [int(orbit) for orbit in orbit_numbers]
                            
                            for orbit in orbit_numbers:
                                self.truncation.append((orbit, occupation))
                        
                        else:
                            """
                            Line does not contain '[' and thus does not
                            contain truncation information.
                            """
                            break

    def _extract_info_from_summary_fname(self):
        """
        Extract nucleus and model space name.
        """
        fname_split = self.fname_summary.split("/")[-1]  # Remove path.
        fname_split = fname_split.split("_")
        self.nucleus = fname_split[1]
        self.model_space = fname_split[2][:-4]  # Remove .txt and keep model space name.

    def _read_summary(self):
        """
        Read energy level data, transition probabilities and transition
        strengths from `KSHELL` output files.

        Raises
        ------
        KshellDataStructureError
            If the `KSHELL` file has unexpected structure / syntax.
        """
        npy_path = "tmp"
        base_fname = self.path.split("/")[-1][:-4]

        try:
            os.mkdir(npy_path)
        except FileExistsError:
            pass

        with open(f"{npy_path}/README.txt", "w") as outfile:
            msg = "This directory contains binary numpy data of KSHELL summary data."
            msg += " The purpose is to speed up subsequent runs which use the same summary data."
            msg += " It is safe to delete this entire directory if you have the original summary text file, "
            msg += "though at the cost of having to read the summary text file over again which may take some time."
            msg += " The ksutil.loadtxt parameter load_and_save_to_file = 'overwrite' will force a re-write of the binary numpy data."
            outfile.write(msg)
        
        unique_id = _generate_unique_identifier(self.path)
        levels_fname = f"{npy_path}/{base_fname}_levels_{unique_id}.npy"
        transitions_BM1_fname = f"{npy_path}/{base_fname}_transitions_BM1_{unique_id}.npy"
        transitions_BE2_fname = f"{npy_path}/{base_fname}_transitions_BE2_{unique_id}.npy"
        transitions_BE1_fname = f"{npy_path}/{base_fname}_transitions_BE1_{unique_id}.npy"
        debug_fname = f"{npy_path}/{base_fname}_debug_{unique_id}.npy"

        fnames = [
            levels_fname, transitions_BE2_fname, transitions_BM1_fname,
            transitions_BE1_fname, debug_fname
        ]

        if self.load_and_save_to_file != "overwrite":
            """
            Do not load files if overwrite parameter has been passed.
            """
            if all([os.path.isfile(fname) for fname in fnames]) and self.load_and_save_to_file:
                """
                If all files exist, load them. If any of the files do
                not exist, all will be generated.
                """
                self.levels = np.load(file=levels_fname, allow_pickle=True)
                self.transitions_BM1 = np.load(file=transitions_BM1_fname, allow_pickle=True)
                self.transitions_BE2 = np.load(file=transitions_BE2_fname, allow_pickle=True)
                self.transitions_BE1 = np.load(file=transitions_BE1_fname, allow_pickle=True)
                self.debug = np.load(file=debug_fname, allow_pickle=True)
                msg = "Summary data loaded from .npy!"
                msg += " Use loadtxt parameter load_and_save_to_file = 'overwrite'"
                msg += " to re-read data from the summary file."
                print(msg)
                return

        parallel_args = [
            [self.fname_summary, "Energy", "replace_this_entry_with_loader", 0],
            [self.fname_summary, "B(E1)", "replace_this_entry_with_loader", 1],
            [self.fname_summary, "B(M1)", "replace_this_entry_with_loader", 2],
            [self.fname_summary, "B(E2)", "replace_this_entry_with_loader", 3],
        ]

        if self.old_or_new == "new":
            parallel_args[0][2] = _load_energy_levels
            parallel_args[1][2] = _load_transition_probabilities
            parallel_args[2][2] = _load_transition_probabilities
            parallel_args[3][2] = _load_transition_probabilities

        elif self.old_or_new == "old":
            parallel_args[0][2] = _load_energy_levels
            parallel_args[1][2] = _load_transition_probabilities_old
            parallel_args[2][2] = _load_transition_probabilities_old
            parallel_args[3][2] = _load_transition_probabilities_old

        elif self.old_or_new == "jem":
            parallel_args[0][2] = _load_energy_levels
            parallel_args[1][2] = _load_transition_probabilities_jem
            parallel_args[2][2] = _load_transition_probabilities_jem
            parallel_args[3][2] = _load_transition_probabilities_jem

        if flags["parallel"]:
            with multiprocessing.Pool() as pool:
                pool_res = pool.map(_generic_loader, parallel_args)
                self.levels, self.negative_spin_counts[0] = pool_res[0]
                self.transitions_BE1, self.negative_spin_counts[1] = pool_res[1]
                self.transitions_BM1, self.negative_spin_counts[2] = pool_res[2]
                self.transitions_BE2, self.negative_spin_counts[3] = pool_res[3]
        else:
            self.levels, self.negative_spin_counts[0] = _generic_loader(parallel_args[0])
            self.transitions_BE1, self.negative_spin_counts[1] = _generic_loader(parallel_args[1])
            self.transitions_BM1, self.negative_spin_counts[2] = _generic_loader(parallel_args[2])
            self.transitions_BE2, self.negative_spin_counts[3] = _generic_loader(parallel_args[3])

        self.levels = np.array(self.levels)
        self.transitions_BE1 = np.array(self.transitions_BE1)
        self.transitions_BM1 = np.array(self.transitions_BM1)
        self.transitions_BE2 = np.array(self.transitions_BE2)
        self.debug = "DEBUG\n"
        self.debug += f"skipped -1 states in levels: {self.negative_spin_counts[0]}\n"
        self.debug += f"skipped -1 states in BE1: {self.negative_spin_counts[1]}\n"
        self.debug += f"skipped -1 states in BM1: {self.negative_spin_counts[2]}\n"
        self.debug += f"skipped -1 states in BE2: {self.negative_spin_counts[3]}\n"
        self.debug = np.array(self.debug)

        if self.old_or_new == "jem":
            """
            'jem style' summary syntax lists all initial and final
            excitation energies in transitions as absolute values.
            Subtract the ground state energy to get the relative
            energies to match the newer KSHELL summary file syntax.
            """
            try:
                self.transitions_BM1[:, 3] -= self.levels[0, 0]
                self.transitions_BM1[:, 7] -= self.levels[0, 0]
            except IndexError:
                """
                No BM1 transitions.
                """
                pass
            try:
                self.transitions_BE1[:, 3] -= self.levels[0, 0]
                self.transitions_BE1[:, 7] -= self.levels[0, 0]
            except IndexError:
                """
                No BE1 transitions.
                """
                pass
            try:
                self.transitions_BE2[:, 3] -= self.levels[0, 0]
                self.transitions_BE2[:, 7] -= self.levels[0, 0]
            except IndexError:
                """
                No BE2 transitions.
                """
                pass

        if self.load_and_save_to_file:
            np.save(file=levels_fname, arr=self.levels, allow_pickle=True)
            np.save(file=transitions_BM1_fname, arr=self.transitions_BM1, allow_pickle=True)
            np.save(file=transitions_BE2_fname, arr=self.transitions_BE2, allow_pickle=True)
            np.save(file=transitions_BE1_fname, arr=self.transitions_BE1, allow_pickle=True)
            np.save(file=debug_fname, arr=self.debug, allow_pickle=True)

    def level_plot(self,
        include_n_states: int = 1000,
        filter_spins: Union[None, list] = None
        ):
        """
        Wrapper method to include level plot as an attribute to this
        class. Generate a level plot for a single isotope. Spin on the x
        axis, energy on the y axis.

        Parameters
        ----------
        include_n_states : int
            The maximum amount of states to plot for each spin. Default
            set to a large number to indicate ≈ no limit.

        filter_spins : Union[None, list]
            Which spins to include in the plot. If `None`, all spins are
            plotted. Defaults to `None`
        """
        level_plot(
            levels = self.levels,
            include_n_states = include_n_states,
            filter_spins = filter_spins
        )

    def level_density_plot(self,
            bin_width: Union[int, float] = 0.2,
            include_n_states: Union[None, int] = None,
            plot: bool = True,
            save_plot: bool = False
        ):
        """
        Wrapper method to include level density plotting as
        an attribute to this class. Generate the level density with the
        input bin size.

        Parameters
        ----------
        See level_density in general_utilities.py for parameter
        information.
        """
        bins, density = level_density(
            levels = self.levels,
            bin_width = bin_width,
            include_n_states = include_n_states,
            plot = plot,
            save_plot = save_plot
        )

        return bins, density

    def nld(self,
        bin_width: Union[int, float] = 0.2,
        include_n_states: Union[None, int] = None,
        plot: bool = True,
        save_plot: bool = False
        ):
        """
        Wrapper method to level_density_plot.
        """
        return self.level_density_plot(
            bin_width = bin_width,
            include_n_states = include_n_states,
            plot = plot,
            save_plot = save_plot
        )

    def gamma_strength_function_average_plot(self,
        bin_width: Union[float, int] = 0.2,
        Ex_min: Union[float, int] = 5,
        Ex_max: Union[float, int] = 50,
        multipole_type: str = "M1",
        prefactor_E1: Union[None, float] = None,
        prefactor_M1: Union[None, float] = None,
        prefactor_E2: Union[None, float] = None,
        initial_or_final: str = "initial",
        partial_or_total: str = "partial",
        include_only_nonzero_in_average: bool = True,
        include_n_states: Union[None, int] = None,
        filter_spins: Union[None, list] = None,
        filter_parities: str = "both",
        porter_thomas: bool = False,
        plot: bool = True,
        save_plot: bool = False
        ):
        """
        Wrapper method to include gamma ray strength function
        calculations as an attribute to this class.

        Parameters
        ----------
        See gamma_strength_function_average in general_utilities.py
        for parameter descriptions.
        """
        transitions_dict = {
            "M1": self.transitions_BM1,
            "E2": self.transitions_BE2,
            "E1": self.transitions_BE1
        }
        return gamma_strength_function_average(
            levels = self.levels,
            transitions = transitions_dict[multipole_type],
            bin_width = bin_width,
            Ex_min = Ex_min,
            Ex_max = Ex_max,
            multipole_type = multipole_type,
            prefactor_E1 = prefactor_E1,
            prefactor_M1 = prefactor_M1,
            prefactor_E2 = prefactor_E2,
            initial_or_final = initial_or_final,
            partial_or_total = partial_or_total,
            include_only_nonzero_in_average = include_only_nonzero_in_average,
            include_n_states = include_n_states,
            filter_spins = filter_spins,
            filter_parities = filter_parities,
            porter_thomas = porter_thomas,
            plot = plot,
            save_plot = save_plot
        )

    def gsf(self,
        bin_width: Union[float, int] = 0.2,
        Ex_min: Union[float, int] = 5,
        Ex_max: Union[float, int] = 50,
        multipole_type: str = "M1",
        prefactor_E1: Union[None, float] = None,
        prefactor_M1: Union[None, float] = None,
        prefactor_E2: Union[None, float] = None,
        initial_or_final: str = "initial",
        partial_or_total: str = "partial",
        include_only_nonzero_in_average: bool = True,
        include_n_states: Union[None, int] = None,
        filter_spins: Union[None, list] = None,
        filter_parities: str = "both",
        porter_thomas: bool = False,
        plot: bool = True,
        save_plot: bool = False
        ):
        """
        Alias for gamma_strength_function_average_plot. See that
        docstring for details.
        """
        return self.gamma_strength_function_average_plot(
            bin_width = bin_width,
            Ex_min = Ex_min,
            Ex_max = Ex_max,
            multipole_type = multipole_type,
            prefactor_E1 = prefactor_E1,
            prefactor_M1 = prefactor_M1,
            prefactor_E2 = prefactor_E2,
            initial_or_final = initial_or_final,
            partial_or_total = partial_or_total,
            include_only_nonzero_in_average = include_only_nonzero_in_average,
            include_n_states = include_n_states,
            filter_spins = filter_spins,
            filter_parities = filter_parities,
            porter_thomas = porter_thomas,
            plot = plot,
            save_plot = save_plot
        )

    def porter_thomas(self, multipole_type: str, **kwargs):
        """
        Wrapper for general_utilities.porter_thomas. See that docstring
        for details.

        Parameters
        ----------
        multipole_type : str
            Choose the multipolarity of the transitions. 'E1', 'M1',
            'E2'.
        """
        transitions_dict = {
            "E1": self.transitions_BE1,
            "M1": self.transitions_BM1,
            "E2": self.transitions_BE2,
        }
        
        return porter_thomas(transitions_dict[multipole_type], **kwargs)

    @property
    def help(self):
        """
        Generate a list of instance attributes without magic and private
        methods.

        Returns
        -------
        help_list : list
            A list of non-magic instance attributes.
        """
        help_list = []
        for elem in dir(self):
            if not elem.startswith("_"):   # Omit magic and private methods.
                help_list.append(elem)
        
        return help_list

    @property
    def parameters(self) -> dict:
        """
        Get the KSHELL parameters from the shell file.

        Returns
        -------
        : dict
            A dictionary of KSHELL parameters.
        """
        path = self.path
        if os.path.isfile(path):
            path = path.rsplit("/", 1)[0]
        return get_parameters(path)

def _process_kshell_output_in_parallel(args):
    """
    Simple wrapper for parallelizing loading of KSHELL files.
    """
    filepath, load_and_save_to_file, old_or_new = args
    print(filepath)
    return ReadKshellOutput(filepath, load_and_save_to_file, old_or_new)

def loadtxt(
    path: str,
    is_directory: bool = False,
    filter_: Union[None, str] = None,
    load_and_save_to_file: Union[bool, str] = True,
    old_or_new = "new"
    ) -> list:
    """
    Wrapper for using ReadKshellOutput class as a function.
    TODO: Consider changing 'path' to 'fname' to be the same as
    np.loadtxt.

    Parameters
    ----------
    path : str
        Filename (and path) of `KSHELL` output data file, or path to
        directory containing sub-directories with `KSHELL` output data.
    
    is_directory : bool
        If True, and 'path' is a directory containing sub-directories
        with `KSHELL` data files, the contents of 'path' will be scanned
        for `KSHELL` data files. Currently supports only summary files.

    filter_ : Union[None, str]
        NOTE: Shouldnt the type be list, not str?

    load_and_save_to_file : Union[bool, str]
        Toggle saving data as `.npy` files on / off. If 'overwrite',
        saved `.npy` files are overwritten.

    old_or_new : str
        Choose between old and new summary file syntax. All summary
        files generated pre 2021-11-24 use old style.
        New:
        J_i  pi_i idx_i Ex_i    J_f  pi_f idx_f Ex_f      dE         B(E2)->         B(E2)->[wu]     B(E2)<-         B(E2)<-[wu]
        5    +    1     0.036   6    +    1     0.000     0.036     70.43477980      6.43689168     59.59865983      5.44660066
        Old:
        J_i    Ex_i     J_f    Ex_f   dE        B(M1)->         B(M1)<- 
        2+(11) 18.393 2+(10) 17.791 0.602 0.1(  0.0) 0.1( 0.0)

    Returns
    -------
    data : list
        List of instances with data from `KSHELL` data file as
        attributes.
    """
    loadtxt_time = time.perf_counter()  # Debug.
    all_fnames = None
    data = []
    if old_or_new not in (old_or_new_allowed := ["old", "new", "jem"]):
        msg = f"'old_or_new' argument must be in {old_or_new_allowed}!"
        msg += f" Got '{old_or_new}'."
        raise ValueError(msg)

    if (is_directory) and (not os.path.isdir(path)):
        msg = f"{path} is not a directory"
        raise NotADirectoryError(msg)

    elif (not is_directory) and (not os.path.isfile(path)):
        msg = f"{path} is not a file"
        raise FileNotFoundError(msg)

    elif (is_directory) and (os.path.isdir(path)):
        msg = "The 'is_directory' option is not properly tested and is"
        msg += " deprecated at the moment. Might return in the future."
        raise DeprecationWarning(msg)
        all_fnames = {}

        for element in sorted(os.listdir(path)):
            """
            List all content in path.
            """
            if os.path.isdir(path + element):
                """
                If element is a directory, enter it to find data files.
                """
                all_fnames[element] = []    # Create blank list entry in dict for current element.
                for isotope in os.listdir(path + element):
                    """
                    List all content in the element directory.
                    """
                    if isotope.startswith("summary") and isotope.endswith(".txt"):
                        """
                        Extract summary data files.
                        """
                        try:
                            """
                            Example: O16.
                            """
                            n_neutrons = int(isotope[9:11])
                        except ValueError:
                            """
                            Example: Ne20.
                            """
                            n_neutrons = int(isotope[10:12])

                        n_neutrons -= atomic_numbers[element.split("_")[1]]
                        all_fnames[element].append([element + "/" + isotope, n_neutrons])
        
        pool = multiprocessing.Pool()
        for key in all_fnames:
            """
            Sort each list in the dict by the number of neutrons. Loop
            over all directories in 'all_fnames' and extract KSHELL data
            and append to a list.
            """
            if filter_ is not None:
                if key.split("_")[1] not in filter_:
                    """
                    Skip elements not in filter_.
                    """
                    continue

            all_fnames[key].sort(key=lambda tup: tup[1])   # Why not do this when directory is listed?
            sub_fnames = all_fnames[key]
            arg_list = [(path + i[0], load_and_save_to_file, old_or_new) for i in sub_fnames]
            data += pool.map(_process_kshell_output_in_parallel, arg_list)

    else:
        """
        Only a single KSHELL data file.
        """
        data.append(ReadKshellOutput(path, load_and_save_to_file, old_or_new))

    if not data:
        msg = "No KSHELL data loaded. Most likely error is that the given"
        msg += f" directory has no KSHELL data files. {path=}"
        raise RuntimeError(msg)

    loadtxt_time = time.perf_counter() - loadtxt_time
    if flags["debug"]:
        print(f"{loadtxt_time = } s")

    return data

def _get_timing_data(path: str):
    """
    Get timing data from KSHELL log files.

    Parameters
    ----------
    path : str
        Path to log file.

    Examples
    --------
    Last 10 lines of log_Ar30_usda_m0p.txt:
    ```
          total      20.899         2    10.44928   1.0000
    pre-process       0.029         1     0.02866   0.0014
        operate       3.202      1007     0.00318   0.1532
     re-orthog.      11.354       707     0.01606   0.5433
  thick-restart       0.214        12     0.01781   0.0102
   diag tri-mat       3.880       707     0.00549   0.1857
           misc       2.220                         0.1062

           tmp        0.002       101     0.00002   0.0001
    ```
    """
    if "log" not in path:
        msg = f"Unknown log file name! Got '{path}'"
        raise KshellDataStructureError(msg)

    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    res = os.popen(f'tail -n 20 {path}').read()    # Get the final 10 lines.
    res = res.split("\n")
    total = None
    
    if "_tr_" not in path:
        """
        KSHELL log.
        """
        for elem in res:
            tmp = elem.split()
            try:
                if tmp[0] == "total":
                    total = float(tmp[1])
                    break
            except IndexError:
                continue
        
    elif "_tr_" in path:
        """
        Transit log.
        """
        for elem in res:
            tmp = elem.split()
            try:
                if tmp[0] == "total":
                    total = float(tmp[3])
                    break
            except IndexError:
                continue

    if total is None:
        msg = f"Not able to extract timing data from '{path}'!"
        raise KshellDataStructureError(msg)
    
    return total

def _get_memory_usage(path: str) -> Union[float, None]:
    """
    Get memory usage from KSHELL log files.

    Parameters
    ----------
    path : str
        Path to a single log file.

    Returns
    -------
    total : float, None
        Memory usage in GB or None if memory usage could not be read.
    """
    total = None
    
    if "tr" not in path:
        """
        KSHELL log.
        """
        with open(path, "r") as infile:
            for line in infile:
                if line.startswith("Total Memory for Lanczos vectors:"):
                    try:
                        total = float(line.split()[-2])
                    except ValueError:
                        msg = f"Error reading memory usage from '{path}'."
                        msg += f" Got '{line.split()[-2]}'."
                        raise KshellDataStructureError(msg)
                    break
        
    elif "tr" in path:
        """
        Transit log. NOTE: Not yet implemented.
        """
        return 0

    if total is None:
        msg = f"Not able to extract memory data from '{path.split('/')[-1]}'!"
        raise KshellDataStructureError(msg)
    
    return total

def _sortkey(filename):
    """
    Key for sorting filenames based on angular momentum and parity.
    Example filename: 'log_Sc44_GCLSTsdpfsdgix5pn_j0n.txt'
    (angular momentum  = 0). 
    """
    tmp = filename.split("_")[-1]
    tmp = tmp.split(".")[0]
    # parity = tmp[-1]
    spin = int(tmp[1:-1])
    # return f"{spin:03d}{parity}"    # Examples: 000p, 000n, 016p, 016n
    return spin

def _get_data_general(
    path: str,
    func: Callable,
    plot: bool
    ):
    """
    General input handling for timing data and memory data.

    Parameters
    ----------
    path : str
        Path to a single log file or path to a directory of log files.

    func : Callable
        _get_timing_data or _get_memory_usage.
    """
    total_negative = []
    total_positive = []
    filenames_negative = []
    filenames_positive = []
    if os.path.isfile(path):
        return func(path)
    
    elif os.path.isdir(path):
        for elem in os.listdir(path):
            """
            Select only log files in path.
            """
            tmp = elem.split("_")
            try:
                if ((tmp[0] == "log") or (tmp[1] == "log")) and elem.endswith(".txt"):
                    tmp = tmp[-1].split(".")
                    parity = tmp[0][-1]
                    if parity == "n":
                        filenames_negative.append(elem)
                    elif parity == "p":
                        filenames_positive.append(elem)
            except IndexError:
                continue
        
        filenames_negative.sort(key=_sortkey)
        filenames_positive.sort(key=_sortkey)

        for elem in filenames_negative:
            total_negative.append(func(f"{path}/{elem}"))
        for elem in filenames_positive:
            total_positive.append(func(f"{path}/{elem}"))
        
        if plot:
            xticks_negative = ["sum"] + [str(Fraction(_sortkey(i)/2)) for i in filenames_negative]
            xticks_positive = ["sum"] + [str(Fraction(_sortkey(i)/2)) for i in filenames_positive]
            sum_total_negative = sum(total_negative)
            sum_total_positive = sum(total_positive)
            
            fig0, ax0 = plt.subplots(ncols=1, nrows=2)
            fig1, ax1 = plt.subplots(ncols=1, nrows=2)

            bars = ax0[0].bar(
                xticks_negative,
                [sum_total_negative/60/60] + [i/60/60 for i in total_negative],
                color = "black",
            )
            ax0[0].set_title("negative")
            for rect in bars:
                height = rect.get_height()
                ax0[0].text(
                    x = rect.get_x() + rect.get_width() / 2.0,
                    y = height,
                    s = f'{height:.3f}',
                    ha = 'center',
                    va = 'bottom'
                )
            
            bars = ax1[0].bar(
                xticks_negative,
                [sum_total_negative/sum_total_negative] + [i/sum_total_negative for i in total_negative],
                color = "black",
            )
            ax1[0].set_title("negative")
            for rect in bars:
                height = rect.get_height()
                ax1[0].text(
                    x = rect.get_x() + rect.get_width() / 2.0,
                    y = height,
                    s = f'{height:.3f}',
                    ha = 'center',
                    va = 'bottom'
                )
            
            bars = ax0[1].bar(
                xticks_positive,
                [sum_total_positive/60/60] + [i/60/60 for i in total_positive],
                color = "black",
            )
            ax0[1].set_title("positive")
            for rect in bars:
                height = rect.get_height()
                ax0[1].text(
                    x = rect.get_x() + rect.get_width() / 2.0,
                    y = height,
                    s = f'{height:.3f}',
                    ha = 'center',
                    va = 'bottom'
                )

            bars = ax1[1].bar(
                xticks_positive,
                [sum_total_positive/sum_total_positive] + [i/sum_total_positive for i in total_positive],
                color = "black",
            )
            ax1[1].set_title("positive")
            for rect in bars:
                height = rect.get_height()
                ax1[1].text(
                    x = rect.get_x() + rect.get_width() / 2.0,
                    y = height,
                    s = f'{height:.3f}',
                    ha = 'center',
                    va = 'bottom'
                )

            fig0.text(x=0.02, y=0.5, s="Time [h]", rotation="vertical")
            fig0.text(x=0.5, y=0.02, s="Angular momentum")
            fig1.text(x=0.02, y=0.5, s="Norm. time", rotation="vertical")
            fig1.text(x=0.5, y=0.02, s="Angular momentum")
            plt.show()

        return sum(total_negative) + sum(total_positive)

    else:
        msg = f"'{path}' is neither a file nor a directory!"
        raise FileNotFoundError(msg)

def get_timing_data(path: str, plot: bool = False) -> float:
    """
    Wrapper for _get_timing_data. Input a single log filename and get
    the timing data. Input a path to a directory several log files and
    get the summed timing data. In units of seconds.

    Parameters
    ----------
    path : str
        Path to a single log file or path to a directory of log files.

    Returns
    -------
    : float
        The summed times for all input log files.
    """
    return _get_data_general(path, _get_timing_data, plot)

def get_memory_usage(path: str) -> float:
    """
    Wrapper for _get_memory_usage. Input a single log filename and get
    the memory data. Input a path to a directory several log files and
    get the summed memory data. In units of GB.

    Parameters
    ----------
    path : str
        Path to a single log file or path to a directory of log files.

    Returns
    -------
    : float
        The summed memory usage for all input log files.
    """
    return _get_data_general(path, _get_memory_usage, False)

def get_parameters(path: str, verbose: bool = True) -> dict:
    """
    Extract the parameters which are fed to KSHELL throught the shell
    script.

    Parameters
    ----------
    path : str
        Path to a KSHELL work directory.

    Returns
    -------
    res : dict
        A dictionary where the keys are the parameter names and the
        values are the corresponding values.
    """
    res = {}
    shell_filename = None
    if os.path.isdir(path):
        for elem in os.listdir(path):
            if elem.endswith(".sh"):
                shell_filename = f"{path}/{elem}"
                break
    else:
        print("Directly specifying path to .sh file not yet implemented!")

    if shell_filename is None:
        if verbose:
            msg = f"No .sh file found in path '{path}'!"
            print(msg)

        return res
    
    with open(shell_filename, "r") as infile:
        for line in infile:
            if line.startswith(r"&input"):
                break
        
        for line in infile:
            if line.startswith(r"&end"):
                """
                End of parameters.
                """
                break
            
            tmp = line.split("=")
            key = tmp[0].strip()
            value = tmp[1].strip()

            try:
                value = ast.literal_eval(value)
            except ValueError:
                """
                Cant convert strings. Keep them as strings.
                """
                pass
            except SyntaxError:
                """
                Cant convert Fortran booleans (.true., .false.). Keep
                them as strings.
                """
                pass
            
            res[key] = value

    return res