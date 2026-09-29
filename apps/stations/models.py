from django.db import models


class FuelStation(models.Model):
    """A truck stop and its retail diesel price, imported from the OPIS price file."""

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=128)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=4, help_text="Retail price in USD per gallon.")
    # Null when the station's city could not be geocoded; such stations are not used for routing.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["state", "city", "name"]
        indexes = [models.Index(fields=["latitude", "longitude"])]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"

    @property
    def is_geocoded(self) -> bool:
        return self.latitude is not None and self.longitude is not None
