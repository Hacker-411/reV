# -*- coding: utf-8 -*-
"""
SAM Wind Balance of System Cost Model
"""

import numpy as np
from PySAM.PySSC import ssc_sim_from_dict
from reV.utilities.exceptions import SAMInputError


class WindBos:
    """Wind Balance of System Cost Model."""

    # keys for the windbos input data dictionary.
    # Some keys may not be found explicitly in the SAM input.
    KEYS = ('tech_model',
            'financial_model',
            'machine_rating',
            'rotor_diameter',
            'hub_height',
            'number_of_turbines',
            'interconnect_voltage',
            'distance_to_interconnect',
            'site_terrain',
            'turbine_layout',
            'soil_condition',
            'construction_time',
            'om_building_size',
            'quantity_test_met_towers',
            'quantity_permanent_met_towers',
            'weather_delay_days',
            'crane_breakdowns',
            'access_road_entrances',
            'turbine_capital_cost',
            'turbine_cost_per_kw',
            'tower_top_mass',
            'delivery_assist_required',
            'pad_mount_transformer_required',
            'new_switchyard_required',
            'rock_trenching_required',
            'mv_thermal_backfill',
            'mv_overhead_collector',
            'performance_bond',
            'contingency',
            'warranty_management',
            'sales_and_use_tax',
            'overhead',
            'profit_margin',
            'development_fee',
            'turbine_transportation')

    def __init__(self, inputs):
        """
        Parameters
        ----------
        inputs : dict
            SAM key value pair inputs.
        """

        self._inputs = inputs
        self._special = {'tech_model': 'windbos',
                         'financial_model': 'none',
                         'machine_rating': self.machine_rating,
                         'hub_height': self.hub_height,
                         'rotor_diameter': self.rotor_diameter,
                         'number_of_turbines': self.number_of_turbines,
                         'turbine_capital_cost': self.turbine_capital_cost,
                         }
        self._turbine_capital_cost = 0.0
        self._parse_inputs()
        self._out = ssc_sim_from_dict(self._datadict)

    def _parse_inputs(self):
        """Parse SAM inputs into a windbos input dict and perform any
        required special operations."""

        self._datadict = {}
        for k in self.KEYS:
            if k in self._special:
                self._datadict[k] = self._special[k]
            elif k not in self._inputs:
                raise SAMInputError('Windbos requires input key: "{}"'
                                    .format(k))
            else:
                self._datadict[k] = self._inputs[k]

    @property
    def machine_rating(self):
        """Single turbine machine rating either from input or power curve."""
        if 'machine_rating' in self._inputs:
            return self._inputs['machine_rating']
        else:
            return np.max(self._inputs['wind_turbine_powercurve_powerout'])

    @property
    def hub_height(self):
        """Turbine hub height."""
        return self._inputs['wind_turbine_hub_ht']

    @property
    def rotor_diameter(self):
        """Turbine rotor diameter."""
        return self._inputs['wind_turbine_rotor_diameter']

    @property
    def number_of_turbines(self):
        """Number of turbines either based on input or system (farm) capacity
        and machine rating"""

        if 'number_of_turbines' in self._inputs:
            return self._inputs['number_of_turbines']
        else:
            return self._inputs['system_capacity'] / self.machine_rating

    @property
    def turbine_capital_cost(self):
        """Returns zero (no turbine capital cost for WindBOS input,
        and assigns any input turbine_capital_cost to an attr"""

        if 'turbine_capital_cost' in self._inputs:
            self._turbine_capital_cost = self._inputs['turbine_capital_cost']
        else:
            self._turbine_capital_cost = 0.0
        return 0.0

    @property
    def bos_cost(self):
        """Get the balance of system cost ($)."""
        return self._out['project_total_budgeted_cost']

    @property
    def turbine_cost(self):
        """Get the turbine cost ($)."""
        tcost = ((self._inputs['turbine_cost_per_kw']
                  * self.machine_rating
                  * self.number_of_turbines)
                 + (self._turbine_capital_cost
                    * self.number_of_turbines))
        return tcost

    @property
    def sales_tax_mult(self):
        """Get a sales tax multiplier (on the total installed cost)."""
        basis = self._inputs.get('sales_tax_basis', 0) / 100
        tax = self._datadict.get('sales_and_use_tax', 0) / 100
        return 1 + (basis * tax)

    @property
    def total_installed_cost(self):
        """Get the total installed cost ($) (bos + turbine)."""
        return (self.bos_cost + self.turbine_cost) * self.sales_tax_mult
