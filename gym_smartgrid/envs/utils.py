import numpy as np
from scipy.stats import norm
import os


DAYS_PER_MONTH = [31, 30, 28, 31, 30, 31, 31, 30, 31, 30, 31, 30]


def init_vre(wind, solar, delta_t, noise_factor=0.1, np_random=None):
    """
    Return generator objects to model wind and solar generation.

    This function returns a python generator object for each generator device
    (wind or solar), modelling a stochastic process.

    :param wind: a dict with {dev_idx: P_max} for wind power generation devices.
    :param solar: a dict with {dev_idx: P_max} for solar power devices.
    :param delta_t: 0.25 if each time step has a 15-min length.
    :param noise_factor: a factor multiplying noise sampled from N(0, 1).
    :return: a list of generator objects, one for each generator.
    """

    return VRESet(wind, solar, delta_t, noise_factor, np_random)

def init_load(factors, delta_t, np_random):

    # Load basic demand curves from files.
    curves_per_month = _load_demand_curves()

    load_generators = LoadSet(delta_t, factors, curves_per_month, np_random)

    return load_generators

def _load_demand_curves():
    """
    Load and return demand curves stored in .csv files.

    This function loads the data stored in folder 'data_demand_curves' as a
    list of ndarray, one for each month of the year (in order). Each array is a
    N_day x 96 matrix, where element [i, j] of the array represents the load
    demand on day i, at timestep j, where each timestep is assumed to last 15
    minutes. The data is normalized to be in [0.2, 0.8].

    :return: a list of 12 ndarray, each containing 29-31 demand curves.
    """

    curves_per_month = []
    for i in range(12):
        current_file = os.path.dirname(__file__)
        filename = os.path.join(current_file,
                                'data_demand_curves',
                               'curves_' +  str(i) + '.csv')
        curves = np.loadtxt(filename, delimiter=',')
        curves_per_month.append(curves)

    return curves_per_month


class DistributedGenerator(object):
    def __init__(self, P_max, delta_t, noise_factor, np_random):
        self.delta_t = delta_t  # 0.25 if time-step is 15 minutes.
        self.P_max = P_max  # installed capacity (MW).
        self.noise_factor = noise_factor  # multiply noise from N(0, 1).
        self.T = 24 * sum(DAYS_PER_MONTH)  # number of hours in a year.
        self.np_random = np_random  # RandomState to seed the random generator.

    def next(self, timestep):
        raise NotImplementedError


class WindGenerator(DistributedGenerator):
    def __init__(self, P_max, delta_t, noise_factor, np_random):
        super().__init__(P_max, delta_t, noise_factor, np_random)

    def next(self, timestep):
        """ Return the next real power generation from the wind farm. """

        hour = (timestep * self.delta_t) % self.T

        # Get a mean capacity factor based on the day of the year (deterministic).
        next_p = self._yearly_pattern(hour)

        # Add random noise sampled from N(0, 1).
        next_p += self.noise_factor * self.np_random.normal(0., scale=1.)

        # Make sure that P stays within [0, 1].
        next_p = next_p if next_p > 0. else 0.
        next_p = next_p if next_p < 1. else 1.

        # Save next real power generation.
        self.p_injection = next_p * self.P_max

        return self.p_injection

    def _yearly_pattern(self, hour):
        """
        Return a factor to scale wind generation, based on the time of the year.

        This function returns a factor in [0.5, 1.0] used to scale the wind
        power generation curves, based on the time of the year, following a
        simple sinusoid. The base hour=0 represents 12:00 a.m. on January,
        1st. The sinusoid is designed to return its minimum value on the
        Summer Solstice and its maximum value on the Winter one.

        :param hour: the number of hours passed January, 1st at 12:00 a.m.
        :return: a wind generation scaling factor in [0, 1].
        """

        # Shift hour to be centered on December, 22nd (Winter Solstice).
        h = hour + 240
        return 0.15 * np.cos(h * 2 * np.pi / self.T) + 0.45


class SolarGenerator(DistributedGenerator):
    def __init__(self, P_max, delta_t, noise_factor, np_random):
        super().__init__(P_max, delta_t, noise_factor, np_random)

    def next(self, timestep):
        """ Return the next real power generation from the solar farm. """

        hour = (timestep * self.delta_t) % self.T

        # Get sunrise and sunset times for the current day.
        self._sunset_sunrise_pattern(hour)

        # Get a mean capacity factor based on the date and time (deterministic).
        next_p = self._bell_curve(hour, self.sunrise, self.sunset) \
                 * self._yearly_pattern(hour)

        # Make sure that P stays within [0, 1].
        next_p = next_p if next_p > 0. else 0.
        next_p = next_p if next_p < 1. else 1.

        # Save next real power generation.
        self.p_injection = next_p * self.P_max

        return self.p_injection

    def _sunset_sunrise_pattern(self, hour):
        """
        Compute the sunset and sunrise hour, based on the date of the year.

        :param hour: the number of hours since January, 1st at 12:00 a.m.
        """

        h = hour + 240
        self.sunset = 1.5 * np.sin(h * 2 * np.pi / self.T) + 18.5
        self.sunrise = 1.5 * np.sin(h * 2 * np.pi / self.T) + 5.5

    def _bell_curve(self, hour, sunrise, sunset):
        """
        Return the a noisy solar generation-like (bell) curve value at hour t.

        This function returns a capacity factor for solar generation following a
        bell curve (Gaussian), given a time of the day. It is assumed that hour=0
        represents 12:00 a.m., i.e. hour=26 => 2:00 a.m. Noise is also added
        sampled from a Gaussian N(0, 1).

        :param hour: hour of the day (hour=1.5 means 1:30 am).
        :param sunrise: hour of sunrise for the given day.
        :param sunset: hour of sunset for the given day.
        :return: the noisy solar generation, normalized in [0, 1].
        """

        h = hour % 24
        y = lambda x: norm.pdf(x, loc=12., scale=2.)
        if h > sunrise and h < sunset:
            p = y(h) / y(12.)
            # Add noise to the capacity factor (stochastic).
            p += self.noise_factor * self.np_random.normal(loc=0., scale=1.)
        else:
            p = 0.
        return p

    def _yearly_pattern(self, hour):
        """
        Return a factor to scale solar generation, based on the time of the year.

        This function returns a factor in [0.5, 1.0] used to scale the solar
        power generation curves, based on the time of the year, following a
        simple sinusoid. The base hour=0 represents 12:00 a.m. on January,
        1st. The sinusoid is designed to return its minimum value on the
        Winter Solstice and its maximum value on the Summer one.

        :param hour: the number of hours passed January, 1st at 12:00 a.m.
        :return: a solar generation scaling factor in [0, 1].
        """

        # Shift hour to be centered on December, 22nd (Winter Solstice).
        h = hour + 240

        return 0.25 * np.sin(h * 2 * np.pi / self.T - np.pi / 2) + 0.75


class VRESet(object):
    def __init__(self, wind, solar, delta_t, noise_factor, np_random):

        # Initialize random generator.
        rng_state = np.random.RandomState() if np_random is None else np_random

        dev_gen = {}

        # Create a generator object for each wind energy resource.
        for dev_idx, Pmax in wind:
            dev_gen[dev_idx] = WindGenerator(Pmax, delta_t, noise_factor, rng_state)

        # Create a generator object for each solar energy resource.
        for dev_idx, Pmax in solar:
            dev_gen[dev_idx] = SolarGenerator(Pmax, delta_t, noise_factor, rng_state)

        # Transform the dictionary into a list, ordered by device index.
        self.generators = []
        for dev_idx in sorted(dev_gen.keys()):
            self.generators.append(dev_gen[dev_idx])

    def next(self, timestep):
        next_p = [vre.next(timestep - 1) for vre in self.generators]



class LoadSet(object):
    def __init__(self, delta_t, factors, basic_curves, np_random):

        self.delta_t = delta_t
        self.month = 0
        self.day = 0
        self.np_random  = np_random

        # Store basic demand curves.
        self.basic_curves = basic_curves

        # Create N_load generator objects to model passive loads.
        self.loads = []
        for factor in factors:
            self.loads.append(LoadGenerator(factor))

    def next(self, timestep):

        # Get the index of the timestep within a single day.
        t_intraday = int((timestep - 1) % (24 / self.delta_t))

        if not t_intraday:

            # Increase the current date.
            self._increase_date()

            for load in self.loads:

                # Select a random day in the month.
                day = self.np_random.randint(0, 31)

                # Generate a new demand curve for each load.
                load.set_daily_curve(day, self.month,
                                     self.basic_curves[self.month][day, :],
                                     self.np_random)

        # Get the next real power injection of each load.
        next_p = []
        for load in self.loads:
            next_p.append(load.next(t_intraday))

        return next_p

    def _increase_date(self):

        self.day += 1
        if self.day >= DAYS_PER_MONTH[self.month]:
            self.month = (self.month + 1) % 12
            self.day = 0


class LoadGenerator(object):
    def __init__(self, factor, ran_factor=0.01):
        self.factor = factor
        self.month = 0
        self.day = 0
        self.ran_factor = ran_factor

    @property
    def month(self):
        return self._month

    @month.setter
    def month(self, value):
        if value < 0 or value > 11:
            raise ValueError('The month index should be in [0, 11] (assuming '
                             '0-indexing).')
        else:
            self._month = value

    @property
    def day(self):
        return self._day

    @day.setter
    def day(self, value):
        if value < 0 or value > 30:
            raise ValueError('The day index should be in [0, 30] (assuming '
                             '0-indexing).')
        else:
            self._day = value

    def set_daily_curve(self, day, month, curve, np_random):

        self.day = day
        self.month = month

        # Add noise sampled from a Gaussian.
        self.demand = curve + self.ran_factor * np_random.normal(size=curve.size)

        # Multiply curve by magnitude factor specific to the passive load.
        self.demand *= self.factor

    def next(self, t_intraday):
        return self.demand[t_intraday]



if __name__ == '__main__':
    pass