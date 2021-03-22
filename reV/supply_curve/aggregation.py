# -*- coding: utf-8 -*-
"""
reV aggregation framework.
"""
from abc import ABC, abstractmethod, abstractclassmethod
from concurrent.futures import as_completed
import h5py
import logging
import numpy as np
import os
import pandas as pd

from reV.handlers.outputs import Outputs
from reV.handlers.exclusions import ExclusionLayers
from reV.supply_curve.exclusions import ExclusionMaskFromDict
from reV.supply_curve.points import (SupplyCurveExtent,
                                     AggregationSupplyCurvePoint)
from reV.utilities.exceptions import (EmptySupplyCurvePointError,
                                      FileInputError, SupplyCurveInputError)

from rex.resource import Resource
from rex.utilities.execution import SpawnProcessPool
from rex.utilities.loggers import log_mem

logger = logging.getLogger(__name__)


class AbstractAggFileHandler(ABC):
    """Simple framework to handle aggregation file context managers."""

    def __init__(self, excl_fpath, excl_dict=None, area_filter_kernel='queen',
                 min_area=None):
        """
        Parameters
        ----------
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        excl_dict : dict, optional
            Dictionary of exclusion LayerMask arugments {layer: {kwarg: value}}
            by default None
        area_filter_kernel : str, optional
            Contiguous area filter method to use on final exclusions mask,
            by default 'queen'
        min_area : float, optional
            Minimum required contiguous area filter in sq-km,
            by default None
        """
        self._excl_fpath = excl_fpath
        self._excl = ExclusionMaskFromDict(excl_fpath, layers_dict=excl_dict,
                                           min_area=min_area,
                                           kernel=area_filter_kernel)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        self.close()
        if type is not None:
            raise

    @abstractmethod
    def close(self):
        """Close all file handlers."""
        self._excl.close()

    @property
    def exclusions(self):
        """Get the exclusions file handler object.

        Returns
        -------
        _excl : ExclusionMask
            Exclusions h5 handler object.
        """
        return self._excl

    @property
    def h5(self):
        """
        Placeholder for h5 Resource handler
        """


class AggFileHandler(AbstractAggFileHandler):
    """
    Framework to handle aggregation file context manager:
    - exclusions .h5 file
    - h5 file to be aggregated
    """

    def __init__(self, excl_fpath, h5_fpath, excl_dict=None,
                 area_filter_kernel='queen', min_area=None):
        """
        Parameters
        ----------
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        h5_fpath : str
            Filepath to .h5 file to be aggregated
        excl_dict : dict, optional
            Dictionary of exclusion LayerMask arugments {layer: {kwarg: value}}
            by default None
        area_filter_kernel : str, optional
            Contiguous area filter method to use on final exclusions mask,
            by default 'queen'
        min_area : float, optional
            Minimum required contiguous area filter in sq-km, by default None
        """
        super().__init__(excl_fpath, excl_dict=excl_dict,
                         area_filter_kernel=area_filter_kernel,
                         min_area=min_area)

        self._h5 = Resource(h5_fpath)

    @property
    def h5(self):
        """
        Get the h5 file handler object.

        Returns
        -------
        _h5 : Outputs
            reV h5 outputs handler object.
        """
        return self._h5

    def close(self):
        """Close all file handlers."""
        self._excl.close()
        self._h5.close()


class AbstractAggregation(ABC):
    """Abstract supply curve points aggregation framework based on only an
    exclusion file and techmap."""

    def __init__(self, excl_fpath, tm_dset, excl_dict=None,
                 area_filter_kernel='queen', min_area=None,
                 resolution=64, excl_area=None, gids=None,
                 pre_extract_inclusions=True):
        """
        Parameters
        ----------
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        tm_dset : str
            Dataset name in the techmap file containing the
            exclusions-to-resource mapping data.
        excl_dict : dict, optional
            Dictionary of exclusion LayerMask arugments {layer: {kwarg: value}}
            by default None
        area_filter_kernel : str, optional
            Contiguous area filter method to use on final exclusions mask,
            by default "queen"
        min_area : float, optional
            Minimum required contiguous area filter in sq-km,
            by default None
        resolution : int, optional
            SC resolution, must be input in combination with gid. Prefered
            option is to use the row/col slices to define the SC point instead,
            by default None
        excl_area : float, optional
            Area of an exclusion pixel in km2. None will try to infer the area
            from the profile transform attribute in excl_fpath, by default None
        gids : list, optional
            List of gids to get summary for (can use to subset if running in
            parallel), or None for all gids in the SC extent, by default None
        pre_extract_inclusions : bool, optional
            Optional flag to pre-extract/compute the inclusion mask from the
            provided excl_dict, by default True
        """
        self._excl_fpath = excl_fpath
        self._tm_dset = tm_dset
        self._excl_dict = excl_dict
        self._resolution = resolution
        self._area_filter_kernel = area_filter_kernel
        self._min_area = min_area
        self._gids = gids
        self._excl_area = self._get_excl_area(excl_fpath, excl_area=excl_area)

        if pre_extract_inclusions:
            self._inclusion_mask = self._extract_inclusion_mask(
                excl_fpath,
                excl_dict=excl_dict,
                area_filter_kernel=area_filter_kernel,
                min_area=min_area)
        else:
            self._inclusion_mask = None

    @property
    def gids(self):
        """
        1D array of supply curve point gids to aggregate

        Returns
        -------
        ndarray
        """
        if self._gids is None:
            with SupplyCurveExtent(self._excl_fpath,
                                   resolution=self._resolution) as sc:
                self._gids = sc.valid_sc_points(self._tm_dset)
        elif np.issubdtype(type(self._gids), np.number):
            self._gids = np.array([self._gids])
        elif not isinstance(self._gids, np.ndarray):
            self._gids = np.array(self._gids)

        return self._gids

    @staticmethod
    def _get_excl_area(excl_fpath, excl_area=None):
        """
        Get exclusion area from excl_fpath pixel area. Confirm that the
        exclusion area is not None.

        Parameters
        ----------
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        excl_area : float, optional
            Area of an exclusion pixel in km2. None will try to infer the area
            from the profile transform attribute in excl_fpath, by default None

        Returns
        -------
        excl_area : float
            Area of an exclusion pixel in km2
        """
        if excl_area is None:
            logger.debug('Setting the exclusion area from the area of a pixel '
                         'in {}'.format(excl_fpath))
            with ExclusionLayers(excl_fpath) as excl:
                excl_area = excl.pixel_area

        if excl_area is None:
            e = ('No exclusion pixel area was input and could not parse '
                 'area from the exclusion file attributes!')
            logger.error(e)
            raise SupplyCurveInputError(e)

        return excl_area

    @staticmethod
    def _extract_inclusion_mask(excl_fpath, excl_dict=None,
                                area_filter_kernel='queen', min_area=None):
        """
        Extract the full inclusion mask from excl_fpath using the given
        exclusion layers and whether or not to run a minimum area filter

        Parameters
        ----------
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        excl_dict : dict, optional
            Dictionary of exclusion LayerMask arugments {layer: {kwarg: value}}
            by default None
        area_filter_kernel : str, optional
            Contiguous area filter method to use on final exclusions mask,
            by default "queen"
        min_area : float, optional
            Minimum required contiguous area filter in sq-km,
            by default None

        Returns
        -------
        inclusion_mask : ndarray
            Pre-computed 2D inclusion mask (normalized with expected range:
            [0, 1], where 1 is included and 0 is excluded)
        """
        logger.info('Pre-extracting full exclusion mask, this could take '
                    'up to 30min for a large exclusion config...')
        inclusion_mask = ExclusionMaskFromDict.run(
            excl_fpath, layers_dict=excl_dict,
            min_area=min_area, kernel=area_filter_kernel)
        logger.info('Finished extracting full exclusion mask.')
        logger.info('The full exclusion mask has {:.2f}% of area included.'
                    .format(100 * inclusion_mask.sum()
                            / inclusion_mask.size))

        if inclusion_mask.sum() == 0:
            msg = 'The exclusions inputs resulted in a fully excluded mask!'
            logger.error(msg)
            raise SupplyCurveInputError(msg)

        return inclusion_mask

    @abstractmethod
    def _check_files(self):
        """Do a preflight check on input files"""

        if not os.path.exists(self._excl_fpath):
            raise FileNotFoundError('Could not find required input file: '
                                    '{}'.format(self._excl_fpath))

        with h5py.File(self._excl_fpath, 'r') as f:
            if self._tm_dset not in f:
                raise FileInputError('Could not find techmap dataset "{}" '
                                     'in exclusions file: {}'
                                     .format(self._tm_dset,
                                             self._excl_fpath))

    @staticmethod
    def _check_inclusion_mask(inclusion_mask, gids, excl_shape):
        """
        Check inclusion mask to ensure it has the proper shape

        Parameters
        ----------
        inclusion_mask : list | ndarray
            List of inclusion masks for each gid or 2D inclusion mask
        gids : list | ndarray
            sc point gids corresponding to inclusion mask
        excl_shape : tuple
            Exclusion layers shape
        """
        if isinstance(inclusion_mask, list):
            assert len(inclusion_mask) == len(gids)
        elif isinstance(inclusion_mask, np.ndarray):
            assert inclusion_mask.shape == excl_shape

    @staticmethod
    def _get_gid_inclusion_mask(inclusion_mask, i, gid, slice_lookup,
                                resolution=64):
        """
        Get inclusion mask for desired gid

        Parameters
        ----------
        inclusion_mask : list | ndarray
            List of inclusion masks for each gid or 2D inclusion mask
        i : int
            Gid index value value, used to extract inclusion mask from a list
            of inclusion masks
        gid : int
            sc_point_gid value, used to extract inclusion mask from 2D
            inclusion array
        slice_lookup : dict
            Mapping of sc_point_gids to exclusion/inclusion row and column
            slices
        resolution : int, optional
            supply curve extent resolution, by default 64

        Returns
        -------
        gid_inclusions : ndarray | None
            2D array of inclusions for desired gid, normalized from 0, excluded
            to 1 fully included, if inclusion mask is None gid_inclusions
            is None
        """
        gid_inclusions = None
        if isinstance(inclusion_mask, list):
            gid_inclusions = inclusion_mask[i]
            assert gid_inclusions.shape[0] <= resolution
            assert gid_inclusions.shape[1] <= resolution
        elif isinstance(inclusion_mask, np.ndarray):
            row_slice, col_slice = slice_lookup[gid]
            gid_inclusions = inclusion_mask[row_slice, col_slice]

        return gid_inclusions

    @abstractclassmethod
    def run_serial(cls, sc_point_method, excl_fpath, tm_dset,
                   excl_dict=None, inclusion_mask=None,
                   area_filter_kernel='queen', min_area=None, resolution=64,
                   gids=None, args=None, kwargs=None):
        """Standalone method to create agg summary - can be parallelized.

        Parameters
        ----------
        sc_point_method : method
            Supply Curve Point Method to operate on a single SC point.
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        tm_dset : str
            Dataset name in the exclusions file containing the
            exclusions-to-resource mapping data.
        excl_dict : dict, optional
            Dictionary of exclusion LayerMask arugments {layer: {kwarg: value}}
            by default None
        inclusion_mask : np.ndarray, optional
            2D array pre-extracted inclusion mask where 1 is included and 0 is
            excluded. This must be either match the full exclusion shape or
            be a list of single-sc-point exclusion masks corresponding to the
            gids input, by default None
        area_filter_kernel : str, optional
            Contiguous area filter method to use on final exclusions mask,
            by default "queen"
        min_area : float, optional
            Minimum required contiguous area filter in sq-km, by default None
        resolution : int, optional
            SC resolution, must be input in combination with gid. Prefered
            option is to use the row/col slices to define the SC point instead,
            by default None
        gids : list, optional
            List of gids to get summary for (can use to subset if running in
            parallel), or None for all gids in the SC extent, by default None
        args : list, optional
            List of positional args for sc_point_method, by default None
        kwargs : dict, optional
            Dict of kwargs for sc_point_method, by default None

        Returns
        -------
        output : list
            List of output objects from sc_point_method.
        """

        if args is None:
            args = []
        if kwargs is None:
            kwargs = {}

        output = []

        with SupplyCurveExtent(excl_fpath, resolution=resolution) as sc:
            exclusion_shape = sc.exclusions.shape
            if gids is None:
                gids = sc.valid_sc_points(tm_dset)
            elif np.issubdtype(type(gids), np.number):
                gids = [gids]

            slice_lookup = sc.get_slice_lookup(gids)

        cls._check_inclusion_mask(inclusion_mask, gids, exclusion_shape)

        # pre-extract handlers so they are not repeatedly initialized
        file_kwargs = {'excl_dict': excl_dict,
                       'area_filter_kernel': area_filter_kernel,
                       'min_area': min_area}
        # pylint: disable=abstract-class-instantiated
        with AbstractAggFileHandler(excl_fpath, **file_kwargs) as fh:

            for i, gid in enumerate(gids):
                gid_inclusions = cls._get_gid_inclusion_mask(
                    inclusion_mask, i, gid, slice_lookup,
                    resolution=resolution)
                try:
                    gid_out = sc_point_method(
                        gid,
                        fh.exclusions,
                        tm_dset,
                        *args,
                        inclusion_mask=gid_inclusions,
                        excl_dict=excl_dict,
                        resolution=resolution,
                        exclusion_shape=exclusion_shape,
                        close=False,
                        **kwargs)

                except EmptySupplyCurvePointError:
                    pass

                except Exception:
                    logger.exception('SC gid {} failed!'.format(gid))
                    raise

                else:
                    output.append(gid_out)

        return output

    @abstractmethod
    def run_parallel(self, sc_point_method, args=None, kwargs=None,
                     max_workers=None, chunk_point_len=1000):
        """
        Aggregate with sc_point_method in parallel

        Parameters
        ----------
        sc_point_method : function
            Function to apply to each supply curve point
        args : list, optional
            List of positional args for sc_point_method, by default None
        kwargs : dict, optional
            Dict of kwargs for sc_point_method, by default None
        max_workers : int, optional
            Number of cores to run summary on. None is all available cpus,
            by default None
        chunk_point_len : int, optional
            Number of SC points to process on a single parallel worker,
            by default 1000

        Returns
        -------
        summary : list
            List of outputs from sc_point_method.
        """
        chunks = np.array_split(
            self.gids, int(np.ceil(len(self.gids) / chunk_point_len)))

        if self._inclusion_mask is not None:
            with SupplyCurveExtent(self._excl_fpath,
                                   resolution=self._resolution) as sc:
                assert sc.exclusions.shape == self._inclusion_mask.shape
                slice_lookup = sc.get_slice_lookup(self.gids)

        logger.info('Running supply curve point aggregation for '
                    'points {} through {} at a resolution of {} '
                    'on {} cores in {} chunks.'
                    .format(self.gids[0], self.gids[-1], self._resolution,
                            max_workers, len(chunks)))

        n_finished = 0
        futures = []
        output = []
        loggers = [__name__, 'reV.supply_curve.points', 'reV']
        with SpawnProcessPool(max_workers=max_workers, loggers=loggers) as exe:

            # iterate through split executions, submitting each to worker
            for gid_set in chunks:
                # submit executions and append to futures list
                chunk_incl_masks = None
                if self._inclusion_mask is not None:
                    chunk_incl_masks = []
                    for gid in gid_set:
                        rs, cs = slice_lookup[gid]
                        chunk_incl_masks.append(self._inclusion_mask[rs, cs])

                futures.append(exe.submit(
                    self.run_serial,
                    sc_point_method, self._excl_fpath, self._tm_dset,
                    excl_dict=self._excl_dict,
                    inclusion_mask=chunk_incl_masks,
                    area_filter_kernel=self._area_filter_kernel,
                    min_area=self._min_area,
                    resolution=self._resolution,
                    gids=gid_set,
                    args=args,
                    kwargs=kwargs))

            # gather results
            for future in as_completed(futures):
                n_finished += 1
                logger.info('Parallel aggregation futures collected: '
                            '{} out of {}'
                            .format(n_finished, len(chunks)))
                output += future.result()

        return output

    def aggregate(self, sc_point_method, args=None, kwargs=None,
                  max_workers=None, chunk_point_len=1000):
        """
        Aggregate with sc_point_method

        Parameters
        ----------
        sc_point_method : function
            Function to apply to each supply curve point
        args : list, optional
            List of positional args for sc_point_method, by default None
        kwargs : dict, optional
            Dict of kwargs for sc_point_method, by default None
        max_workers : int, optional
            Number of cores to run summary on. None is all available cpus,
            by default None
        chunk_point_len : int, optional
            Number of SC points to process on a single parallel worker,
            by default 1000

        Returns
        -------
        summary : list
            List of outputs from sc_point_method.
        """
        if max_workers is None:
            max_workers = os.cpu_count()

        if max_workers == 1:
            agg = self.run_serial(sc_point_method, self._excl_fpath,
                                  self._tm_dset,
                                  excl_dict=self._excl_dict,
                                  area_filter_kernel=self._area_filter_kernel,
                                  min_area=self._min_area,
                                  resolution=self._resolution,
                                  gids=self.gids,
                                  args=args,
                                  kwargs=kwargs)
        else:
            agg = self.run_parallel(sc_point_method, args=args,
                                    kwargs=kwargs, max_workers=max_workers,
                                    chunk_point_len=chunk_point_len)

        if not any(agg):
            e = ('Supply curve aggregation found no non-excluded SC points. '
                 'Please check your exclusions or subset SC GID selection.')
            logger.error(e)
            raise EmptySupplyCurvePointError(e)

        return agg

    @classmethod
    def run(cls, excl_fpath, tm_dset, sc_point_method, excl_dict=None,
            area_filter_kernel='queen', min_area=None,
            resolution=64, gids=None, excl_area=None,
            pre_extract_inclusions=True, args=None, kwargs=None,
            max_workers=None, chunk_point_len=1000):
        """Get the supply curve points aggregation summary.

        Parameters
        ----------
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        tm_dset : str
            Dataset name in the techmap file containing the
            exclusions-to-resource mapping data.
        sc_point_method : method
            Supply Curve Point Method to operate on a single SC point.
        excl_dict : dict, optional
            Dictionary of exclusion LayerMask arugments {layer: {kwarg: value}}
            by default None
        area_filter_kernel : str, optional
            Contiguous area filter method to use on final exclusions mask,
            by default "queen"
        min_area : float, optional
            Minimum required contiguous area filter in sq-km, by default None
        resolution : int, optional
            SC resolution, must be input in combination with gid. Prefered
            option is to use the row/col slices to define the SC point instead,
            by default 64
        gids : list, optional
            List of gids to get summary for (can use to subset if running in
            parallel), or None for all gids in the SC extent, by default None
        excl_area : float, optional
            Area of an exclusion pixel in km2. None will try to infer the area
            from the profile transform attribute in excl_fpath,
            by default None
        pre_extract_inclusions : bool, optional
            Optional flag to pre-extract/compute the inclusion mask from the
            provided excl_dict, by default True
        args : list, optional
            List of positional args for sc_point_method, by default None
        kwargs : dict, optional
            Dict of kwargs for sc_point_method, by default None
        max_workers : int, optional
            Number of cores to run summary on. None is all available cpus,
            by default None
        chunk_point_len : int, optional
            Number of SC points to process on a single parallel worker,
            by default 1000

        Returns
        -------
        summary : DataFrame
            Summary of the SC points.
        """

        agg = cls(excl_fpath, tm_dset, excl_dict=excl_dict,
                  area_filter_kernel=area_filter_kernel, min_area=min_area,
                  resolution=resolution, excl_area=excl_area, gids=gids,
                  pre_extract_inclusions=pre_extract_inclusions)

        aggregation = agg.aggregate(sc_point_method, args=args, kwargs=kwargs,
                                    max_workers=max_workers,
                                    chunk_point_len=chunk_point_len)

        return aggregation


class Aggregation(AbstractAggregation):
    """Concrete but generalized aggregation framework to aggregate ANY reV h5
    file to a supply curve grid (based on an aggregated exclusion grid)."""

    def __init__(self, excl_fpath, h5_fpath, tm_dset, *agg_dset,
                 excl_dict=None, area_filter_kernel='queen', min_area=None,
                 resolution=64, excl_area=None, gids=None,
                 pre_extract_inclusions=True):
        """
        Parameters
        ----------
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        h5_fpath : str
            Filepath to .h5 file to aggregate
        tm_dset : str
            Dataset name in the techmap file containing the
            exclusions-to-resource mapping data.
        agg_dset : str
            Dataset to aggreate, can supply multiple datasets
        excl_dict : dict, optional
            Dictionary of exclusion LayerMask arugments {layer: {kwarg: value}}
            by default None
        area_filter_kernel : str, optional
            Contiguous area filter method to use on final exclusions mask,
            by default "queen"
        min_area : float, optional
            Minimum required contiguous area filter in sq-km,
            by default None
        resolution : int, optional
            SC resolution, must be input in combination with gid. Prefered
            option is to use the row/col slices to define the SC point instead,
            by default None
        excl_area : float, optional
            Area of an exclusion pixel in km2. None will try to infer the area
            from the profile transform attribute in excl_fpath,
            by default None
        gids : list, optional
            List of gids to get summary for (can use to subset if running in
            parallel), or None for all gids in the SC extent, by default None
        pre_extract_inclusions : bool, optional
            Optional flag to pre-extract/compute the inclusion mask from the
            provided excl_dict, by default True
        """
        super().__init__(excl_fpath, tm_dset, excl_dict=excl_dict,
                         area_filter_kernel=area_filter_kernel,
                         min_area=min_area, resolution=resolution,
                         excl_area=excl_area, gids=gids,
                         pre_extract_inclusions=pre_extract_inclusions)

        self._h5_fpath = h5_fpath
        if isinstance(agg_dset, str):
            agg_dset = (agg_dset, )

        self._agg_dsets = agg_dset

        self._check_files()
        self._gen_index = self._parse_gen_index(self._h5_fpath)

    def _check_files(self):
        """Do a preflight check on input files"""

        if not os.path.exists(self._excl_fpath):
            raise FileNotFoundError('Could not find required exclusions file: '
                                    '{}'.format(self._excl_fpath))

        if not os.path.exists(self._h5_fpath):
            raise FileNotFoundError('Could not find required h5 file: '
                                    '{}'.format(self._h5_fpath))

        with h5py.File(self._excl_fpath, 'r') as f:
            if self._tm_dset not in f:
                raise FileInputError('Could not find techmap dataset "{}" '
                                     'in exclusions file: {}'
                                     .format(self._tm_dset,
                                             self._excl_fpath))

        with Resource(self._h5_fpath) as f:
            for dset in self._agg_dsets:
                if dset not in f:
                    raise FileInputError('Could not find provided dataset "{}"'
                                         ' in h5 file: {}'
                                         .format(dset, self._h5_fpath))

    @staticmethod
    def _parse_gen_index(h5_fpath):
        """Parse gen outputs for an array of generation gids corresponding to
        the resource gids.

        Parameters
        ----------
        h5_fpath : str
            Filepath to reV compliant .h5 file

        Returns
        -------
        gen_index : np.ndarray
            Array of generation gids with array index equal to resource gid.
            Array value is -1 if the resource index was not used in the
            generation run.
        """

        with Resource(h5_fpath) as f:
            gen_index = f.meta

        if 'gid' in gen_index:
            gen_index = gen_index.rename(columns={'gid': 'res_gids'})
            gen_index['gen_gids'] = gen_index.index
            gen_index = gen_index[['res_gids', 'gen_gids']]
            gen_index = gen_index.set_index(keys='res_gids')
            gen_index = \
                gen_index.reindex(range(int(gen_index.index.max() + 1)))
            gen_index = gen_index['gen_gids'].values
            gen_index[np.isnan(gen_index)] = -1
            gen_index = gen_index.astype(np.int32)
        else:
            gen_index = None

        return gen_index

    @classmethod
    def run_serial(cls, excl_fpath, h5_fpath, tm_dset, *agg_dset,
                   agg_method='mean', excl_dict=None, inclusion_mask=None,
                   area_filter_kernel='queen', min_area=None,
                   resolution=64, excl_area=None, gids=None,
                   gen_index=None):
        """
        Standalone method to aggregate - can be parallelized.

        Parameters
        ----------
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        h5_fpath : str
            Filepath to .h5 file to aggregate
        tm_dset : str
            Dataset name in the techmap file containing the
            exclusions-to-resource mapping data.
        agg_dset : str
            Dataset to aggreate, can supply multiple datasets
        agg_method : str, optional
            Aggregation method, either mean or sum/aggregate, by default "mean"
        excl_dict : dict, optional
            Dictionary of exclusion LayerMask arugments {layer: {kwarg: value}}
            by default None
        area_filter_kernel : str, optional
            Contiguous area filter method to use on final exclusions mask,
            by default "queen"
        min_area : float, optional
            Minimum required contiguous area filter in sq-km,
            by default None
        resolution : int, optional
            SC resolution, must be input in combination with gid. Prefered
            option is to use the row/col slices to define the SC point instead,
            by default None
        excl_area : float, optional
            Area of an exclusion pixel in km2. None will try to infer the area
            from the profile transform attribute in excl_fpath,
            by default None
        gids : list, optional
            List of gids to get summary for (can use to subset if running in
            parallel), or None for all gids in the SC extent, by default None
        gen_index : np.ndarray, optional
            Array of generation gids with array index equal to resource gid.
            Array value is -1 if the resource index was not used in the
            generation run, by default None

        Returns
        -------
        agg_out : dict
            Aggregated values for each aggregation dataset
        """
        with SupplyCurveExtent(excl_fpath, resolution=resolution) as sc:
            exclusion_shape = sc.exclusions.shape
            if gids is None:
                gids = sc.valid_sc_points(tm_dset)
            elif np.issubdtype(type(gids), np.number):
                gids = [gids]

            slice_lookup = sc.get_slice_lookup(gids)

        cls._check_inclusion_mask(inclusion_mask, gids, exclusion_shape)

        # pre-extract handlers so they are not repeatedly initialized
        file_kwargs = {'excl_dict': excl_dict,
                       'area_filter_kernel': area_filter_kernel,
                       'min_area': min_area}
        dsets = agg_dset + ('meta', )
        agg_out = {ds: [] for ds in dsets}
        with AggFileHandler(excl_fpath, h5_fpath, **file_kwargs) as fh:
            n_finished = 0
            for i, gid in enumerate(gids):
                gid_inclusions = cls._get_gid_inclusion_mask(
                    inclusion_mask, i, gid, slice_lookup,
                    resolution=resolution)
                try:
                    gid_out = AggregationSupplyCurvePoint.run(
                        gid,
                        fh.exclusions,
                        fh.h5,
                        tm_dset,
                        *agg_dset,
                        agg_method=agg_method,
                        excl_dict=excl_dict,
                        inclusion_mask=gid_inclusions,
                        resolution=resolution,
                        excl_area=excl_area,
                        exclusion_shape=exclusion_shape,
                        close=False,
                        gen_index=gen_index)

                except EmptySupplyCurvePointError:
                    logger.debug('SC gid {} is fully excluded or does not '
                                 'have any valid source data!'.format(gid))
                except Exception:
                    logger.exception('SC gid {} failed!'.format(gid))
                    raise
                else:
                    n_finished += 1
                    logger.debug('Serial aggregation: '
                                 '{} out of {} points complete'
                                 .format(n_finished, len(gids)))
                    log_mem(logger)
                    for k, v in gid_out.items():
                        agg_out[k].append(v)

        return agg_out

    def run_parallel(self, agg_method='mean', excl_area=None,
                     max_workers=None, chunk_point_len=1000):
        """
        Aggregate in parallel

        Parameters
        ----------
        agg_method : str, optional
            Aggregation method, either mean or sum/aggregate, by default "mean"
        excl_area : float, optional
            Area of an exclusion cell (square km), by default None
        max_workers : int, optional
            Number of cores to run summary on. None is all available cpus,
            by default None
        chunk_point_len : int, optional
            Number of SC points to process on a single parallel worker,
            by default 1000

        Returns
        -------
        agg_out : dict
            Aggregated values for each aggregation dataset
        """
        chunks = np.array_split(
            self.gids, int(np.ceil(len(self.gids) / chunk_point_len)))

        if self._inclusion_mask is not None:
            with SupplyCurveExtent(self._excl_fpath,
                                   resolution=self._resolution) as sc:
                assert sc.exclusions.shape == self._inclusion_mask.shape
                slice_lookup = sc.get_slice_lookup(self.gids)

        logger.info('Running supply curve point aggregation for '
                    'points {} through {} at a resolution of {} '
                    'on {} cores in {} chunks.'
                    .format(self.gids[0], self.gids[-1], self._resolution,
                            max_workers, len(chunks)))

        n_finished = 0
        futures = []
        dsets = self._agg_dsets + ('meta', )
        agg_out = {ds: [] for ds in dsets}
        loggers = [__name__, 'reV.supply_curve.points', 'reV']
        with SpawnProcessPool(max_workers=max_workers, loggers=loggers) as exe:
            # iterate through split executions, submitting each to worker
            for gid_set in chunks:
                # submit executions and append to futures list
                chunk_incl_masks = None
                if self._inclusion_mask is not None:
                    chunk_incl_masks = []
                    for gid in gid_set:
                        rs, cs = slice_lookup[gid]
                        chunk_incl_masks.append(self._inclusion_mask[rs, cs])

                # submit executions and append to futures list
                futures.append(exe.submit(
                    self.run_serial,
                    self._excl_fpath,
                    self._h5_fpath,
                    self._tm_dset,
                    *self._agg_dsets,
                    agg_method=agg_method,
                    excl_dict=self._excl_dict,
                    inclusion_mask=chunk_incl_masks,
                    area_filter_kernel=self._area_filter_kernel,
                    min_area=self._min_area,
                    resolution=self._resolution,
                    excl_area=excl_area,
                    gids=gid_set,
                    gen_index=self._gen_index))

            # gather results
            for future in futures:
                n_finished += 1
                logger.info('Parallel aggregation futures collected: '
                            '{} out of {}'
                            .format(n_finished, len(chunks)))
                for k, v in future.result().items():
                    if v:
                        agg_out[k].extend(v)

        return agg_out

    def aggregate(self, agg_method='mean', max_workers=None,
                  chunk_point_len=1000):
        """
        Aggregate with given agg_method

        Parameters
        ----------
        agg_method : str, optional
            Aggregation method, either mean or sum/aggregate, by default "mean"
        max_workers : int, optional
            Number of cores to run summary on. None is all available cpus,
            by default None
        chunk_point_len : int, optional
            Number of SC points to process on a single parallel worker,
            by default 1000

        Returns
        -------
        agg : dict
            Aggregated values for each aggregation dataset
        """
        if max_workers is None:
            max_workers = os.cpu_count()

        if max_workers == 1:
            agg = self.run_serial(self._excl_fpath,
                                  self._h5_fpath,
                                  self._tm_dset,
                                  *self._agg_dsets,
                                  agg_method=agg_method,
                                  excl_dict=self._excl_dict,
                                  inclusion_mask=self._inclusion_mask,
                                  area_filter_kernel=self._area_filter_kernel,
                                  min_area=self._min_area,
                                  resolution=self._resolution,
                                  excl_area=self._excl_area,
                                  gen_index=self._gen_index)
        else:
            agg = self.run_parallel(agg_method=agg_method,
                                    excl_area=self._excl_area,
                                    max_workers=max_workers,
                                    chunk_point_len=chunk_point_len)

        if not agg['meta']:
            e = ('Supply curve aggregation found no non-excluded SC points. '
                 'Please check your exclusions or subset SC GID selection.')
            logger.error(e)
            raise EmptySupplyCurvePointError(e)

        for k, v in agg.items():
            if k == 'meta':
                v = pd.concat(v, axis=1).T
                v = v.sort_values('sc_point_gid')
                v = v.reset_index(drop=True)
                v.index.name = 'sc_gid'
                agg[k] = v.reset_index()
            else:
                v = np.dstack(v)[0]
                if v.shape[0] == 1:
                    v = v.flatten()

                agg[k] = v

        return agg

    def save_agg_to_h5(self, out_fpath, aggregation):
        """
        Save aggregated data to disc in .h5 format

        Parameters
        ----------
        out_fpath : str
            Output .h5 file path
        aggregation : dict
            Aggregated values for each aggregation dataset
        """
        agg_out = aggregation.copy()
        meta = agg_out.pop('meta')
        for c in meta.columns:
            try:
                meta[c] = pd.to_numeric(meta[c])
            except (ValueError, TypeError):
                pass

        dsets = []
        shapes = {}
        attrs = {}
        chunks = {}
        dtypes = {}
        time_index = None
        with Resource(self._h5_fpath) as f:
            for dset, data in agg_out.items():
                dsets.append(dset)
                shape = data.shape
                shapes[dset] = shape
                if len(data.shape) == 2:
                    if ('time_index' in f) and (shape[0] == f.shape[0]):
                        if time_index is None:
                            time_index = f.time_index

                attrs[dset] = f.get_attrs(dset=dset)
                _, dtype, chunk = f.get_dset_properties(dset)
                chunks[dset] = chunk
                dtypes[dset] = dtype

        Outputs.init_h5(out_fpath, dsets, shapes, attrs, chunks, dtypes,
                        meta, time_index=time_index)

        with Outputs(out_fpath, mode='a') as out:
            for dset, data in agg_out.items():
                out[dset] = data

    @classmethod
    def run(cls, excl_fpath, h5_fpath, tm_dset, *agg_dset,
            excl_dict=None, area_filter_kernel='queen', min_area=None,
            resolution=64, excl_area=None, gids=None,
            pre_extract_inclusions=True, agg_method='mean', max_workers=None,
            chunk_point_len=1000, out_fpath=None):
        """Get the supply curve points aggregation summary.

        Parameters
        ----------
        excl_fpath : str
            Filepath to exclusions h5 with techmap dataset.
        h5_fpath : str
            Filepath to .h5 file to aggregate
        tm_dset : str
            Dataset name in the techmap file containing the
            exclusions-to-resource mapping data.
        agg_dset : str
            Dataset to aggreate, can supply multiple datasets
        excl_dict : dict, optional
            Dictionary of exclusion LayerMask arugments {layer: {kwarg: value}}
            by default None
        area_filter_kernel : str, optional
            Contiguous area filter method to use on final exclusions mask,
            by default "queen"
        min_area : float, optional
            Minimum required contiguous area filter in sq-km,
            by default None
        resolution : int, optional
            SC resolution, must be input in combination with gid. Prefered
            option is to use the row/col slices to define the SC point instead,
            by default None
        excl_area : float, optional
            Area of an exclusion pixel in km2. None will try to infer the area
            from the profile transform attribute in excl_fpath,
            by default None
        gids : list, optional
            List of gids to get summary for (can use to subset if running in
            parallel), or None for all gids in the SC extent, by default None
        pre_extract_inclusions : bool, optional
            Optional flag to pre-extract/compute the inclusion mask from the
            provided excl_dict, by default True
        agg_method : str, optional
            Aggregation method, either mean or sum/aggregate, by default "mean"
        max_workers : int, optional
            Number of cores to run summary on. None is all available cpus,
            by default None
        chunk_point_len : int, optional
            Number of SC points to process on a single parallel worker,
            by default 1000
        out_fpath : str, optional
            Output .h5 file path, by default None

        Returns
        -------
        agg : dict
            Aggregated values for each aggregation dataset
        """

        agg = cls(excl_fpath, h5_fpath, tm_dset, *agg_dset,
                  excl_dict=excl_dict, area_filter_kernel=area_filter_kernel,
                  min_area=min_area, resolution=resolution,
                  excl_area=excl_area, gids=gids,
                  pre_extract_inclusions=pre_extract_inclusions)

        aggregation = agg.aggregate(agg_method=agg_method,
                                    max_workers=max_workers,
                                    chunk_point_len=chunk_point_len)

        if out_fpath is not None:
            agg.save_agg_to_h5(out_fpath, aggregation)

        return aggregation
