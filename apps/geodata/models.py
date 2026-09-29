from django.db import models


class Place(models.Model):
    """A US city/town with its internal point, loaded from the Census Gazetteer files.

    Used to geocode fuel stations and "City, ST" route inputs without calling
    an external geocoding API at request time.
    """

    class Source(models.TextChoices):
        PLACE = "place", "Census place"
        PLACE_ALIAS = "place_alias", "Alternate name of a Census place"
        COUNTY_SUBDIVISION = "county_subdivision", "Census county subdivision (township/town)"

    # When two sources produce the same name in a state, the higher priority wins.
    SOURCE_PRIORITY = {Source.PLACE: 3, Source.PLACE_ALIAS: 2, Source.COUNTY_SUBDIVISION: 1}

    state = models.CharField(max_length=2)
    name = models.CharField(max_length=128)
    key = models.CharField(max_length=128, help_text="Normalized lookup key, see normalization.place_key().")
    latitude = models.FloatField()
    longitude = models.FloatField()
    land_area_sq_mi = models.FloatField(default=0)
    source = models.CharField(max_length=32, choices=Source.choices)

    class Meta:
        ordering = ["state", "name"]
        constraints = [models.UniqueConstraint(fields=["state", "key"], name="unique_place_key_per_state")]

    def __str__(self):
        return f"{self.name}, {self.state}"
