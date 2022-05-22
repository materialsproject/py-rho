from __future__ import annotations

import warnings
from dataclasses import dataclass

"""Chang Density Objects: Periodic Grid + Lattice / Atoms"""
import math
from abc import ABCMeta, abstractmethod
from typing import Dict, List, Tuple, Union

import numpy as np
import numpy.typing as npt
from monty.dev import deprecated
from monty.json import MSONable
from pymatgen.core.lattice import Lattice
from pymatgen.core.structure import Structure
from pymatgen.io.vasp import Chgcar, Poscar, VolumetricData

from pyrho.core.pgrid import PGrid


class ChargeABC(metaclass=ABCMeta):
    def __init__(self, pgrids: dict[str, PGrid], normalization: str):
        self.pgrids = pgrids
        self.normalization = normalization

    def __post_init__(self):
        """Post initialization:
        Checks:
            - Make sure all the lattices are identical
        """
        lattices = [self.pgrids[key].lattice for key in self.pgrids.keys()]
        if not all(np.allclose(lattices[0], lattice) for lattice in lattices):
            raise ValueError("Lattices are not identical")
        self.lattice = lattices[0]

    def get_reshaped(
        self,
        sc_mat: npt.ArrayLike,
        grid_out: npt.ArrayLike | int,
        origin: npt.ArrayLike = (0.0, 0.0, 0.0),
    ) -> "ChargeABC":
        """
        Reshape the charge density data to a new grid.
        """

    @abstractmethod
    def reorient_axis(self) -> None:
        pass


@dataclass
class ChargeDensity(MSONable):
    """
    Defines a charge density with a PGrid object along with the atomic structure

    Args:
        grid_data: Volumetric data to read in
        structure: Atomic structure corresponding to the charge density
        normalization: the normalization scheme:
            - 'vasp' sum of the data / number of grid points == number of electrons
            - None/"none" no normalization
    """

    pgrids: Dict[str, PGrid]
    structure: Structure
    normalization: str | None = "vasp"

    def __post_init__(self):
        """Post initialization:
        Steps:
            - Make sure all the lattices are identical
            - Perform the normalization of the grid data

        """
        lattices = [self.pgrids[key].lattice for key in self.pgrids.keys()]
        if not all(
            np.allclose(self.structure.lattice, lattice) for lattice in lattices
        ):
            raise ValueError("Lattices are not identical")
        for k, v in self.pgrids.items():
            self.pgrids[k] = self._normalize_data(v.grid_data)

    def _normalize_data(self, grid_data: npt.NDArray) -> npt.NDArray:
        """Normalize the data to the number of electrons

        The standard charge density from VASP is given as (rho*V) such that:
        sum(rho)/NGRID = NELECT/UC_vol
        so the real rho is:
        rho = (rho*UC_vol)*NGRID/UC_vol/UC_vol
        where the second V account for the different number of electrons in
        different cells

        Args:
            grid_data: The grid data to normalize

        Returns:
            NDArray: The normalized grid data
        """
        if self.normalization is None or self.normalization[0].lower() == "n":
            return grid_data
        elif self.normalization[0].lower() == "v":
            return grid_data / self.structure.volume
        else:
            raise NotImplementedError("Not a valid normalization scheme")

    def _scale_data(
        self, data: npt.NDArray, normalization: str | None = "vasp"
    ) -> npt.NDArray:
        """
        Undo the normalization of the data

        Args:
            data: The data to undo the normalization

        Returns:
            NDArray: The data with the normalization undone
        """
        if normalization is None or normalization[0].lower() == "n":
            return data
        elif normalization[0].lower() == "v":
            return data * self.structure.volume
        else:
            raise NotImplementedError("Not a valid normalization scheme")

    @property
    def lattice(self) -> np.ndarray:  # type: ignore
        return self.structure.lattice.matrix

    @classmethod
    def from_pmg(cls, vdata: VolumetricData, data_key="total") -> "ChargeDensity":
        """
        Read a single key from the data field of a VolumetricData object
        Args:
            vdata: The volumetric data object
            data_key: The key to read from in the data field

        Returns:
            ChargeDensity object
        """
        return cls(
            grid_data=vdata.data[data_key],
            structure=vdata.structure,
            normalization="vasp",
        )

    def reorient_axis(self) -> None:
        """
        Change the orientation of the lattice vector so that:
        a points along the x-axis, b is in the xy-plane, c is in the positive-z halve of space
        """
        args: Tuple[float, float, float, float, float, float] = (
            self.structure.lattice.abc + self.structure.lattice.angles
        )
        self.structure.lattice = Lattice.from_parameters(*args, vesta=True)

    # def get_data_in_cube(self, s: float, ngrid: int) -> np.ndarray:
    #     """
    #     Return the charge density data sampled on a cube.
    #
    #     Args:
    #         s: side lengthy in angstroms
    #         ngrid: number of grid points in each direction
    #
    #     Returns:
    #         ndarray: regridded data in a ngrid x ngrid x ngrid array
    #
    #     """
    #     grid_out = [ngrid, ngrid, ngrid]
    #     target_sc_lat_vecs = np.eye(3, 3) * s
    #     sc_mat = np.linalg.inv(self.structure.lattice.matrix) @ target_sc_lat_vecs
    #     _, res = get_sc_interp(self.rho, sc_mat, grid_out)
    #     return res.reshape(grid_out)

    def get_transformed(
        self,
        sc_mat: npt.ArrayLike,
        origin: npt.ArrayLike,
        grid_out: Union[List[int], int],
        up_sample: int = 1,
    ) -> "ChargeDensity":
        """
        Modify the structure and data and return a new object containing the reshaped
        data
        Args:
            sc_mat: Matrix to create the new cell
            frac_shift: translation to be applied on the cell after the matrix
            transformation
            grid_out: density of the new grid, can also just take the desired
            dimension as a list.

        Returns:
            (ChargeDensity) Transformed charge density object

        """

        # warning if the sc_mat is not integer valued
        if not np.allclose(np.round(sc_mat), sc_mat):
            warnings.warn(
                "The `sc_mat` is not integer valued.\n"
                "Non-integer valued transformations are valid but will not product periodic structures, thus we cannot define a new Structure object.\n"
                "We will round the sc_mat to integer values for now but can implement functionality that returns a Molecule object in the future.",
            )
        sc_mat = np.round(sc_mat).astype(int)
        new_structure = self.structure.copy()
        new_structure.translate_sites(
            list(range(len(new_structure))), -np.array(origin)
        )
        new_structure = new_structure * sc_mat

        # determine the output grid
        lengths = new_structure.lattice.abc
        if isinstance(grid_out, int):
            ngrid = grid_out / new_structure.volume
            mult = (np.prod(lengths) / ngrid) ** (1 / 3)
            grid_out = [int(math.floor(max(l_ / mult, 1))) for l_ in lengths]
        else:
            grid_out = grid_out

        new_rho = self.get_transformed_data(
            sc_mat, origin, grid_out=grid_out, up_sample=up_sample
        )
        return ChargeDensity.from_rho(new_rho, new_structure, self.normalization)

    def get_reshaped(
        self,
        sc_mat: npt.ArrayLike,
        grid_out: Union[List, int],
        origin: npt.ArrayLike = (0.0, 0.0, 0.0),
        up_sample: int = 1,
    ) -> "ChargeDensity":
        return self.get_transformed(
            sc_mat=sc_mat, origin=origin, grid_out=grid_out, up_sample=up_sample
        )

    def to_Chgcar(self) -> Chgcar:
        """Convert the charge density to a pymatgen.io.vasp.outputs.Chgcar object

        Scale and convert each key in the pgrids dictionary and create a Chgcar object

        Returns:
            Chgcar: The charge density object
        """
        struct = self.structure.copy()
        data_dict = {}
        for k, v in self.pgrids.items():
            data_dict[k] = self._scale_data(v, normalization="vasp")
        return Chgcar(Poscar(struct), data=data_dict)

    #
    #     _, new_rho = get_sc_interp(self.rho, sc_mat, grid_sizes=grid_out)
    #     new_rho = new_rho.reshape(grid_out)
    #
    #     grid_shifts = [
    #         int(t * g) for t, g in zip(translation - np.round(translation), grid_out)
    #     ]
    #
    #     new_rho = roll_array(new_rho, grid_shifts)
    #     return self.__class__.from_rho(new_rho, new_structure)


# class SpinChargeDensity(MSONable, ChargeABC):
#     def __init__(self, chargeden_dict: Dict, aug_charge: Dict = None):
#         """
#         Wrapper class that parses multiple sets of grid data on the same lattice

#         Args:
#             chargeden_dict: A dictionary containing multiple charge density objects
#                         typically in the format {'total' : ChargeDen1, 'diff' : ChargeDen2}
#         """
#         self.chargeden_dict = chargeden_dict
#         self.aug_charge = aug_charge
#         self._tmp_key = next(
#             iter(self.chargeden_dict)
#         )  # get one key in the dictionary to make writing the subsequent code easier

#     @classmethod
#     def from_pmg_volumetric_data(
#         cls, vdata: VolumetricData, data_keys=("total", "diff")
#     ):
#         chargeden_dict = {}
#         data_aug = getattr(vdata, "data_aug", None)
#         for k in data_keys:
#             chargeden_dict[k] = ChargeDensity.from_pmg(vdata, data_key=k)
#         return cls(chargeden_dict, aug_charge=data_aug)

#     @property
#     def lattice(self) -> Lattice:
#         return self.chargeden_dict[self._tmp_key].lattice

#     def to_Chgcar(self) -> Chgcar:
#         struct = self.chargeden_dict[self._tmp_key].structure
#         data_ = {k: v.renormalized_data for k, v in self.chargeden_dict.items()}
#         return Chgcar(Poscar(struct), data_, data_aug=self.aug_charge)

#     def to_VolumetricData(self) -> VolumetricData:
#         key_ = next(iter(self.chargeden_dict))
#         struct = self.chargeden_dict[key_].structure
#         data_ = {k: v.renormalized_data for k, v in self.chargeden_dict.items()}
#         return VolumetricData(struct, data_)

#     def get_reshaped(
#         self,
#         sc_mat: npt.ArrayLike,
#         grid_out: Union[List, int],
#         origin: npt.ArrayLike = (0.0, 0.0, 0.0),
#         up_sample: int = 1,
#     ) -> "SpinChargeDensity":
#         new_spin_charge = {}
#         for k, v in self.chargeden_dict.items():
#             new_spin_charge[k] = v.get_reshaped_cell(sc_mat, frac_shift, grid_out)
#         factor = int(
#             new_spin_charge[self._tmp_key].structure.num_sites
#             / self.chargeden_dict[self._tmp_key].structure.num_sites
#         )
#         new_aug = {}
#         if self.aug_charge is not None:
#             for k, v in self.aug_charge.items():
#                 new_aug[k] = multiply_aug(v, factor)
#         return self.__class__(new_spin_charge, new_aug)

#     def reorient_axis(self) -> None:
#         for k, v in self.chargeden_dict:
#             v.reorient_axis()


@deprecated
def multiply_aug(data_aug: List[str], factor: int) -> List[str]:
    """
    The original idea here was to use to to speed up some vasp calculations for
    supercells by initializing the entire CHGCAR file.
    The current code does not deal with transformation of the Augementation charges after regridding.

    This is a naive way to multiply the Augmentation data in the CHGCAR,
    a real working implementation will require analysis of the PAW projection operators.
    However, even with such an implementation, the speed up will be minimal due to VASP's internal
    minimization algorithms.
    Args:
        data_aug: The original augmentation data from a CHGCAR
        factor: The multiplication factor (some integer number of times it gets repeated)
    Returns:
        List of strings for each line of the Augmentation data.
    """
    res: List[str] = []
    cur_block: List[str] = []
    cnt = 0
    for ll in data_aug:
        if "augmentation" in ll:
            if cur_block:
                for _ in range(factor):
                    cnt += 1
                    cur_block[
                        0
                    ] = f"augmentation occupancies{cnt:>4}{cur_block[0].split()[-1]:>4}\n"
                    res.extend(cur_block)
            cur_block = [ll]
        else:
            cur_block.append(ll)
    else:
        for _ in range(factor):
            cnt += 1
            cur_block[
                0
            ] = f"augmentation occupancies{cnt:>4}{cur_block[0].split()[-1]:>4}\n"
            res.extend(cur_block)
    return res